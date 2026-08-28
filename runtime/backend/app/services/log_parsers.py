import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from dateutil import parser as date_parser

from app.core.utils import mask_sensitive
from app.services.parser_registry import ParsedEvent, registry


TIMESTAMP_PATTERNS = [
    re.compile(r"(?P<ts>20\d{2}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}[:;]\d{2}(?:[.,]\d{1,6})?)"),
    re.compile(r"(?P<ts>\b\d{2}:\d{2}[:;]\d{2}(?:[.,]\d{1,6})?\b)"),
    re.compile(r"(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"),
]
LEVEL_PATTERN = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERR(?:OR)?|CRIT(?:ICAL)?|FATAL|ALERT|EMERG)\b", re.I)
COLLECT_COMMAND_PATTERN = re.compile(
    r"Start\s+run\s+collect\s+command\s*:\s*(?:(?P<scope>[A-Za-z0-9_-]+)\s*:\s*)?(?P<command>.+?)\s*$",
    re.I | re.M,
)
HUAWEI_RUNTIME_LOG_PATTERN = re.compile(
    r"^\s*(?P<level>TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERR(?:OR)?|CRIT(?:ICAL)?|FATAL|ALERT|EMERG)\s+"
    r"(?P<ts>20\d{2}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}[:;]\d{2}(?:[.,]\d{1,6})?)"
    r"(?P<context>(?:\s*\[[^\]\r\n]*\])*)\s*(?P<message>.*)$",
    re.I | re.M,
)

RULES = [
    ("KERNEL_OOPS", "KERNEL", "kernel", "CRITICAL", re.compile(r"kernel panic|oops:|BUG: unable|call trace|segmentation fault", re.I)),
    ("PROCESS_CRASH", "SYSTEM", "process", "ERROR", re.compile(r"segfault|core dumped|aborted|process .* died|signal 11", re.I)),
    ("WATCHDOG_RESTART", "SYSTEM", "watchdog", "WARN", re.compile(r"watchdog.*(?:restart|timeout|reset)|restarting service", re.I)),
    ("HOSTAPD_START_FAILED", "WLAN", "hostapd", "ERROR", re.compile(r"hostapd.*(?:failed|error)|failed to set beacon|could not configure driver", re.I)),
    ("WLAN_DISCONNECTED", "WLAN", "wifi", "WARN", re.compile(r"deauth|disassoc|disconnect|wlan\d+.*down", re.I)),
    ("DHCP_FAILED", "LAN", "dhcp", "ERROR", re.compile(r"dhcp.*(?:fail|timeout|nak)|no lease|discover timeout", re.I)),
    ("PPPOE_FAILED", "WAN", "pppoe", "ERROR", re.compile(r"pppoe.*(?:fail|timeout|terminated|authentication)|PADI timeout|(?:PAP|CHAP).*fail", re.I)),
    ("AUTH_FAILED", "WLAN", "authentication", "ERROR", re.compile(r"authentication failed|4-way handshake failed|eap.*fail", re.I)),
    ("WAN_LINK_DOWN", "WAN", "network", "WARN", re.compile(r"wan.*link.*down|carrier lost|no carrier", re.I)),
    ("PON_LOS", "PON", "pon", "CRITICAL", re.compile(r"\bLOS\b|loss of signal|pon.*down|optical.*alarm", re.I)),
    ("OMCI_ERROR", "PON", "omci", "ERROR", re.compile(r"omci.*(?:error|fail|timeout|mismatch)", re.I)),
    ("TR069_ERROR", "MANAGEMENT", "tr069", "ERROR", re.compile(r"(?:tr-?069|cwmp).*(?:error|fail|timeout|reject|invalid)", re.I)),
    ("MEMORY_PRESSURE", "SYSTEM", "memory", "ERROR", re.compile(r"out of memory|oom-killer|cannot allocate memory|memory leak", re.I)),
    ("CONFIG_INVALID", "CONFIG", "config", "ERROR", re.compile(r"invalid config|configuration error|missing parameter|invalid value", re.I)),
    ("INTERFACE_STATE_CHANGE", "NETWORK", "interface", "INFO", re.compile(r"(?:eth|wan|lan|wlan|br-|pon)\S*.*(?:link is|state).*\b(up|down)\b", re.I)),
]


def _normalize_level(raw: str | None, fallback: str = "INFO") -> str:
    if not raw:
        return fallback
    level = raw.upper()
    if level in {"ERR", "ERROR"}:
        return "ERROR"
    if level in {"WARN", "WARNING"}:
        return "WARN"
    if level in {"CRIT", "CRITICAL", "FATAL", "ALERT", "EMERG"}:
        return "CRITICAL"
    return level


def _extract_timestamp(line: str, date_hint: str | None = None) -> tuple[str | None, str | None]:
    for pattern in TIMESTAMP_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        raw = match.group("ts")
        try:
            normalized_raw = raw.replace(";", ":").replace(",", ".")
            is_time_only = bool(re.fullmatch(r"\d{2}:\d{2}[:;]\d{2}(?:[.,]\d+)?", raw))
            if is_time_only and not date_hint:
                return raw, None
            raw_for_parse = f"{date_hint} {normalized_raw}" if is_time_only else normalized_raw
            value = date_parser.parse(raw_for_parse, fuzzy=True)
            return raw, value.isoformat()
        except (ValueError, OverflowError):
            return raw, None
    return None, None


def _component_from_path(path: str) -> tuple[str, str]:
    lower = path.lower()
    mapping = [
        (("hostapd",), ("WLAN", "hostapd")),
        (("wlan", "wifi", "wireless", "wap"), ("WLAN", "wifi")),
        (("dhcp", "dnsmasq"), ("LAN", "dhcp")),
        (("pppoe", "ppp"), ("WAN", "pppoe")),
        (("omci",), ("PON", "omci")),
        (("tr069", "cwmp"), ("MANAGEMENT", "tr069")),
        (("kernel", "dmesg", "kmsg"), ("KERNEL", "kernel")),
        (("pon", "optical"), ("PON", "pon")),
    ]
    for keys, value in mapping:
        if any(key in lower for key in keys):
            return value
    return "SYSTEM", Path(path).stem.lower()


def _entities(line: str) -> dict:
    entities: dict[str, str] = {}
    interface = re.search(r"\b((?:wlan|eth|wan|lan|br-|ppp|pon)\w*[.-]?\w*)\b", line, re.I)
    if interface:
        entities["interface"] = interface.group(1)
    error_code = re.search(
        r"(?:errno|error(?:\s+code)?|ret(?:urn)?)\s*[:=]?\s*(-?\d+)(?![\d/-])",
        line,
        re.I,
    )
    if error_code:
        entities["error_code"] = error_code.group(1)
    pid = re.search(r"\bpid[=: ]+(\d+)\b", line, re.I)
    if pid:
        entities["pid"] = pid.group(1)
    return entities


def _matching_rule(line: str) -> tuple[str, str, str, str] | None:
    for code, module, component, fallback_level, pattern in RULES:
        if pattern.search(line):
            return code, module, component, fallback_level
    return None


class GenericLogParser:
    parser_id = "generic-log"
    parser_version = "1.1"

    def probe(self, path: Path, sample: str) -> float:
        score = 0.25
        if path.suffix.lower() in {".log", ".txt", ".out", ".err", ".trace", ".conf", ".cfg"}:
            score += 0.25
        if any(pattern.search(sample) for pattern in TIMESTAMP_PATTERNS):
            score += 0.2
        if LEVEL_PATTERN.search(sample):
            score += 0.1
        return score

    def parse(self, path: Path, relative_path: str, text: str) -> list[ParsedEvent]:
        return list(self.parse_lines(path, relative_path, text.splitlines(), text[:10000]))

    def parse_lines(
        self,
        path: Path,
        relative_path: str,
        lines: Iterable[str],
        sample: str = "",
    ) -> Iterator[ParsedEvent]:
        module_by_path, component_by_path = _component_from_path(relative_path)
        date_hint_match = re.search(r"20\d{2}[-/]\d{2}[-/]\d{2}", sample[:10000])
        date_hint = date_hint_match.group(0).replace("/", "-") if date_hint_match else None
        pending: ParsedEvent | None = None

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            ts_raw, ts_norm = _extract_timestamp(line, date_hint)
            level_match = LEVEL_PATTERN.search(line)
            rule_match = _matching_rule(line)

            # Treat indented stack lines as part of previous event.
            if pending and not ts_raw and (line.startswith((" ", "\t")) or re.search(r"\bat\s+0x[0-9a-f]+|#\d+", line, re.I)):
                pending.raw_text += "\n" + mask_sensitive(line)
                pending.message += " | " + mask_sensitive(line.strip())[:300]
                pending.line_end = line_number
                continue

            if pending:
                yield pending
                pending = None

            if rule_match:
                code, module, component, fallback_level = rule_match
                confidence = 0.92
            else:
                code = "GENERIC_LOG"
                module, component = module_by_path, component_by_path
                fallback_level = "INFO"
                confidence = 0.55

            level = _normalize_level(level_match.group(1) if level_match else None, fallback_level)
            # Store all warning/error lines and selected informational state transitions.
            should_store = rule_match is not None or level in {"WARN", "ERROR", "CRITICAL", "FATAL"}
            if not should_store:
                continue

            pending = ParsedEvent(
                source_file=relative_path,
                line_start=line_number,
                line_end=line_number,
                timestamp_raw=ts_raw,
                timestamp_normalized=ts_norm,
                level=level,
                module=module,
                component=component,
                event_code=code,
                message=mask_sensitive(line.strip())[:2000],
                raw_text=mask_sensitive(line)[:10000],
                entities=_entities(line),
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                confidence=confidence,
            )
        if pending:
            yield pending


class HuaweiCollectDebugInfoParser:
    parser_id = "huawei-collectdebuginfo"
    parser_version = "1.0"

    def probe(self, path: Path, sample: str) -> float:
        score = 0.0
        if "collectdebuginfo" in path.name.lower():
            score += 0.35
        if COLLECT_COMMAND_PATTERN.search(sample):
            score += 0.5
        if re.search(r"get\s+WLANConfiguration!", sample, re.I):
            score += 0.15
        if HUAWEI_RUNTIME_LOG_PATTERN.search(sample):
            score += 0.55
        return min(score, 0.99)

    def parse(self, path: Path, relative_path: str, text: str) -> list[ParsedEvent]:
        return list(self.parse_lines(path, relative_path, text.splitlines(), text[:10000]))

    def parse_lines(
        self,
        path: Path,
        relative_path: str,
        lines: Iterable[str],
        sample: str = "",
    ) -> Iterator[ParsedEvent]:
        pending_runtime: ParsedEvent | None = None

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue

            command_match = COLLECT_COMMAND_PATTERN.search(line)
            if command_match:
                if pending_runtime:
                    yield pending_runtime
                    pending_runtime = None
                scope = (command_match.group("scope") or "DEVICE").upper()
                command = command_match.group("command").strip()
                masked_command = mask_sensitive(command)
                module, component = _component_from_path(scope)
                yield ParsedEvent(
                    source_file=relative_path,
                    line_start=line_number,
                    line_end=line_number,
                    timestamp_raw=None,
                    timestamp_normalized=None,
                    level="INFO",
                    module=module,
                    component=component,
                    event_code="COLLECT_COMMAND",
                    message=f"{scope}: {masked_command}"[:2000],
                    raw_text=mask_sensitive(line)[:10000],
                    entities={"scope": scope, "command": masked_command[:1000]},
                    parser_id=self.parser_id,
                    parser_version=self.parser_version,
                    confidence=0.98,
                )
                continue

            runtime_match = HUAWEI_RUNTIME_LOG_PATTERN.match(line)
            if runtime_match:
                if pending_runtime:
                    yield pending_runtime
                ts_raw, ts_norm = _extract_timestamp(runtime_match.group("ts"))
                raw_level = runtime_match.group("level")
                level = _normalize_level(raw_level)
                context_tokens = re.findall(r"\[([^\]]*)\]", runtime_match.group("context"))
                component_token = next(
                    (token.strip() for token in reversed(context_tokens) if token.strip() and not token.strip().isdigit()),
                    "runtime",
                )
                module, default_component = _component_from_path(component_token)
                component = component_token.lower() if component_token != "runtime" else default_component
                rule_match = _matching_rule(line)
                if rule_match:
                    code, module, component, fallback_level = rule_match
                    level = _normalize_level(raw_level, fallback_level)
                    confidence = 0.96
                else:
                    code = "HUAWEI_RUNTIME_LOG"
                    confidence = 0.9

                message = runtime_match.group("message").strip() or line.strip()
                entities = _entities(line)
                if context_tokens:
                    entities["contexts"] = mask_sensitive(",".join(context_tokens))[:500]
                pending_runtime = ParsedEvent(
                    source_file=relative_path,
                    line_start=line_number,
                    line_end=line_number,
                    timestamp_raw=ts_raw,
                    timestamp_normalized=ts_norm,
                    level=level,
                    module=module,
                    component=component,
                    event_code=code,
                    message=mask_sensitive(message)[:2000],
                    raw_text=mask_sensitive(line)[:10000],
                    entities=entities,
                    parser_id=self.parser_id,
                    parser_version=self.parser_version,
                    confidence=confidence,
                )
                continue

            if pending_runtime and line.startswith((" ", "\t")):
                pending_runtime.raw_text += "\n" + mask_sensitive(line)[:10000]
                pending_runtime.message += " | " + mask_sensitive(line.strip())[:300]
                pending_runtime.line_end = line_number
                continue

            if pending_runtime:
                yield pending_runtime
                pending_runtime = None

        if pending_runtime:
            yield pending_runtime


class JsonLineParser(GenericLogParser):
    parser_id = "json-line"
    parser_version = "1.0"

    def probe(self, path: Path, sample: str) -> float:
        return 0.85 if path.suffix.lower() in {".json", ".jsonl"} and sample.lstrip().startswith(("{", "[")) else 0.1


registry.register(JsonLineParser())
registry.register(HuaweiCollectDebugInfoParser())
registry.register(GenericLogParser())
