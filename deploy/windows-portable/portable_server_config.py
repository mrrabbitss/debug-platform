"""Validated LAN deployment profile shared by the installer and portable runtime."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerConfig:
    public_url: str
    data_root: str
    server_id: str
    schema_version: int = 1
    backend_port: int = 18080
    gateway_bind: str = "0.0.0.0"
    tls_mode: str = "internal"
    certificate_file: str = ""
    certificate_key_file: str = ""
    job_workers: int = 2
    embedding_concurrency: int = 1
    reranker_concurrency: int = 1
    model_threads: int = 8
    model_queue_limit: int = 32
    minimum_free_gib: int = 10
    schema_profile: str = "i7-14700-32gb-pilot"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.public_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("Server public_url must be an HTTPS origin without a path or credentials")
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
                raise ValueError("Use an ASCII DNS name or an IP address") from None
        if self.schema_version != 1 or not re.fullmatch(r"GWAP-[a-f0-9]{32}", self.server_id):
            raise ValueError("Unsupported server configuration or invalid server_id")
        if not Path(self.data_root).is_absolute():
            raise ValueError("Server data_root must be absolute")
        if not 1024 <= self.backend_port <= 65535 or parsed.port == self.backend_port:
            raise ValueError("Backend port must be distinct from HTTPS and between 1024 and 65535")
        ipaddress.ip_address(self.gateway_bind)
        if self.tls_mode not in {"internal", "certificate"}:
            raise ValueError("tls_mode must be internal or certificate")
        if self.tls_mode == "certificate" and not all(
            Path(value).is_absolute() for value in (self.certificate_file, self.certificate_key_file)
        ):
            raise ValueError("Certificate and key must have absolute paths")
        for value, maximum in ((self.job_workers, 4), (self.embedding_concurrency, 8),
                               (self.reranker_concurrency, 8), (self.model_threads, 64),
                               (self.model_queue_limit, 256), (self.minimum_free_gib, 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Server resource limits must be bounded positive integers")

    @property
    def origin(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def root(self) -> Path:
        return Path(self.data_root).resolve()

    @property
    def env_file(self) -> Path:
        return self.root / "config" / "server.env"

    def environment(self) -> dict[str, str]:
        host = urlsplit(self.origin).hostname
        authority = urlsplit(self.origin).netloc
        return {
            "DEPLOYMENT_MODE": "lan_server", "SERVER_INSTANCE_ID": self.server_id,
            "APP_ENV": "prod", "AUTH_MODE": "rbac", "AUTH_ALLOW_LEGACY_ADMIN": "false",
            "API_KEY": "", "MCP_BEARER_TOKEN": "", "MCP_ENABLED": "true",
            "MCP_PUBLIC_BASE_URL": self.origin, "MCP_ALLOWED_HOSTS": authority,
            "MCP_ALLOWED_ORIGINS": self.origin, "CORS_ORIGINS": self.origin,
            "TRUSTED_HOSTS": f"{host},127.0.0.1,localhost",
            "JOB_WORKERS": str(self.job_workers), "MODEL_CPU_THREADS": str(self.model_threads),
            "EMBEDDING_CONCURRENCY": str(self.embedding_concurrency),
            "RERANKER_CONCURRENCY": str(self.reranker_concurrency),
            "MODEL_QUEUE_LIMIT": str(self.model_queue_limit),
            "MINIMUM_FREE_STORAGE_BYTES": str(self.minimum_free_gib * 1024**3),
        }

    def apply_environment(self) -> None:
        os.environ.update(self.environment())

    def payload(self) -> dict:
        return asdict(self)


def load_server_config(path: Path) -> ServerConfig:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {item.name for item in fields(ServerConfig)}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Unknown server configuration field")
    return ServerConfig(**value)


def caddy_configuration(config: ServerConfig) -> dict:
    """JSON avoids interpreting user-controlled paths as Caddyfile directives."""
    parsed = urlsplit(config.origin)
    port = parsed.port or 443
    bind = config.gateway_bind
    address = f"[{bind}]:{port}" if ":" in bind else f"{bind}:{port}"
    tls: dict = {}
    if config.tls_mode == "internal":
        tls["automation"] = {"policies": [{"subjects": [parsed.hostname], "issuers": [{"module": "internal"}]}]}
    else:
        tls["certificates"] = {"load_files": [{"certificate": config.certificate_file, "key": config.certificate_key_file}]}
    return {
        "admin": {"disabled": True},
        # Runtime reverse-proxy errors can contain arbitrary request headers.
        # Remove the request object rather than relying on a finite secret-name list.
        "logging": {"logs": {"default": {"encoder": {
            "format": "filter", "wrap": {"format": "json"},
            "fields": {"request": {"filter": "delete"}},
        }}}},
        "storage": {"module": "file_system", "root": str(config.root / "gateway")},
        "apps": {
            "pki": {"certificate_authorities": {"local": {"install_trust": False}}},
            "tls": tls,
            "http": {"servers": {"platform": {
                "listen": [address], "tls_connection_policies": [{}],
                "automatic_https": {"disable_redirects": True},
                "routes": [{"match": [{"host": [parsed.hostname]}], "handle": [
                    {"handler": "headers", "response": {"set": {
                        "X-Content-Type-Options": ["nosniff"],
                        "Referrer-Policy": ["same-origin"],
                        "Content-Security-Policy": ["frame-ancestors 'self'; object-src 'none'; base-uri 'self'"],
                    }}},
                    {"handler": "reverse_proxy", "upstreams": [{"dial": f"127.0.0.1:{config.backend_port}"}],
                     "flush_interval": -1},
                ]}],
            }}},
        },
    }
