from pathlib import Path

from app.services.log_parsers import GenericLogParser


def test_parser_extracts_hostapd_failure(tmp_path: Path):
    path = tmp_path / "hostapd.log"
    text = "2026-07-20 10:32:18 ERROR hostapd: Failed to set beacon parameters errno=-22\n"
    events = GenericLogParser().parse(path, "wifi/hostapd.log", text)
    assert len(events) == 1
    assert events[0].event_code == "HOSTAPD_START_FAILED"
    assert events[0].entities["error_code"] == "-22"
