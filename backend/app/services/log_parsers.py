import re
from pathlib import Path

from charset_normalizer import from_bytes
from dateutil import parser as date_parser

from app.core.config import get_settings
from app.core.utils import mask_sensitive
from app.services.parser_registry import ParsedEvent, registry


TIMESTAMP_PATTERNS = [
    re.compile(r"(?P<ts>20\d{2}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?)"),
    re.compile(r"(?P<ts>\b\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?\b)"),
    re.compile(r"(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"),
]
LEVEL_PATTERN = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERR(?:OR)?|CRIT(?:ICAL)?|FATAL|ALERT|EMERG)\b", re.I)

RULES = [
    ("KERNEL_OOPS", "KERNEL", "kernel", "CRITICAL", re.compile(r"kernel panic|oops:|BUG: unable|call trace|segmentation fault", re.I)),
    ("PROCESS_CRASH", "SYSTEM", "process", "ERROR", re.compile(r"segfault|core dumped|aborted|process .* died|signal 11", re.I)),
    ("WATCHDOG_RESTART", "SYSTEM", "watchdog", "WARN", re.compile(r"watchdog.*(?:restart|timeout|reset)|restarting service", re.I)),
    ("HOSTAPD_START_FAILED", "WLAN", "hostapd", "ERROR", re.compile(r"hostapd.*(?:failed|error)|failed to set beacon|could not configure driver", re.I)),
    ("WLAN_DISCONNECTED", "WLAN", "wifi", "WARN", re.compile(r"deauth|disassoc|disconnect|wlan\d+.*down", re.I)),
    ("AUTH_FAILED", "WLAN", "authentication", "ERROR", re.compile(r"authentication failed|4-way handshake failed|eap.*fail", re.I)),
    ("DHCP_FAILED", "LAN", "dhcp", "ERROR", re.compile(r"dhcp.*(?:fail|timeout|nak)|no lease|discover timeout", re.I)),
    ("PPPOE_FAILED", "WAN", "pppoe", "ERROR", re.compile(r"pppoe.*(?:fail|timeout|terminated)|PADI timeout|authentication failed", re.I)),
    ("WAN_LINK_DOWN", "WAN", "network", "WARN", re.compile(r"wan.*link.*down|carrier lost|no carrier", re.I)),
    ("PON_LOS", "PON", "pon", "CRITICAL", re.compile(r"\bLOS\b|loss of signal|pon.*down|optical.*alarm", re.I)),
    ("OMCI_ERROR", "PON", "omci", "ERROR", re.compile(r"omci.*(?:error|fail|timeout|mismatch)", re.I)),
    ("TR069_ERROR", "MANAGEMENT", "tr069", "ERROR", re.compile(r"tr-?069|cwmp", re.I)),
    ("MEMORY_PRESSURE", "SYSTEM", "memory", "ERROR", re.compile(r"out of memory|oom-killer|cannot allocate memory|memory leak", re.I)),
    ("CONFIG_INVALID", "CONFIG", "config", "ERROR", re.compile(r"invalid config|configuration error|missing parameter|invalid value", re.I)),
    ("INTERFACE_STATE_CHANGE", "NETWORK", "interface", "INFO", re.compile(r"(?:eth|wan|lan|wlan|br-|pon)\S*.*(?:link is|state).*\b(up|down)\b", re.I)),
]


def read_text_file(path: Path) -> str | None:
    if path.stat().st_size > get_settings().parser_max_text_bytes:
        return None
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return None
    match = from_bytes(raw).best()
    if match is None:
        return raw.decode("utf-8", errors="replace")
    return str(match)


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
            if re.fullmatch(r"\d{2}:\d{2}:\d{2}(?:[.,]\d+)?", raw) and date_hint:
                raw_for_parse = f"{date_hint} {raw.replace(',', '.')}"
            else:
                raw_for_parse = raw.replace(",", ".")
            value = date_parser.parse(raw_for_parse, fuzzy=True)
            return raw, value.isoformat()
        except (ValueError, OverflowError):
            return raw, None
    return None, None


def _component_from_path(path: str) -> tuple[str, str]:
    lower = path.lower()
    mapping = [
        (("hostapd", "wlan", "wifi", "wireless"), ("WLAN", "hostapd")),
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
    error_code = re.search(r"(?:errno|error|ret(?:urn)?)\s*[:=]?\s*(-?\d+)", line, re.I)
    if error_code:
        entities["error_code"] = error_code.group(1)
    pid = re.search(r"\bpid[=: ]+(\d+)\b", line, re.I)
    if pid:
        entities["pid"] = pid.group(1)
    return entities


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
        events: list[ParsedEvent] = []
        module_by_path, component_by_path = _component_from_path(relative_path)
        date_hint_match = re.search(r"20\d{2}[-/]\d{2}[-/]\d{2}", text[:10000])
        date_hint = date_hint_match.group(0).replace("/", "-") if date_hint_match else None
        pending: ParsedEvent | None = None

        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            ts_raw, ts_norm = _extract_timestamp(line, date_hint)
            level_match = LEVEL_PATTERN.search(line)
            rule_match = None
            for code, module, component, fallback_level, pattern in RULES:
                if pattern.search(line):
                    rule_match = (code, module, component, fallback_level)
                    break

            # Treat indented stack lines as part of previous event.
            if pending and not ts_raw and (line.startswith((" ", "\t")) or re.search(r"\bat\s+0x[0-9a-f]+|#\d+", line, re.I)):
                pending.raw_text += "\n" + mask_sensitive(line)
                pending.message += " | " + mask_sensitive(line.strip())[:300]
                pending.line_end = line_number
                continue

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
            events.append(pending)
        return events


class JsonLineParser(GenericLogParser):
    parser_id = "json-line"
    parser_version = "1.0"

    def probe(self, path: Path, sample: str) -> float:
        return 0.85 if path.suffix.lower() in {".json", ".jsonl"} and sample.lstrip().startswith(("{", "[")) else 0.1


registry.register(JsonLineParser())
registry.register(GenericLogParser())
