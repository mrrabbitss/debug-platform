from pathlib import Path

from app.services.log_parsers import GenericLogParser
from app.services.parser_registry import registry


def test_parser_extracts_hostapd_failure(tmp_path: Path):
    path = tmp_path / "hostapd.log"
    text = "2026-07-20 10:32:18 ERROR hostapd: Failed to set beacon parameters errno=-22\n"
    events = GenericLogParser().parse(path, "wifi/hostapd.log", text)
    assert len(events) == 1
    assert events[0].event_code == "HOSTAPD_START_FAILED"
    assert events[0].entities["error_code"] == "-22"


def test_huawei_collectdebuginfo_parser_handles_extensionless_file_and_notice_time(tmp_path: Path):
    path = tmp_path / "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490"
    text = """============================================================
Start run collect command:WAP:get wlan basic laninst 1 wlaninst6
get wlan basic laninst 1 wlaninst6
get WLANConfiguration!
Name      :ath5
Enable    :0
NOTICE 2026-03-02 03:29:17.483[90][DC]DC radio state initialized
ERROR 2026-03-02 03:29:18.004[91][WAP]hostapd: Failed to set beacon parameters errno=-22
"""

    parser = registry.select(path, text)
    events = parser.parse(path, path.name, text)

    assert parser.parser_id == "huawei-collectdebuginfo"
    assert [event.event_code for event in events] == [
        "COLLECT_COMMAND",
        "HUAWEI_RUNTIME_LOG",
        "HOSTAPD_START_FAILED",
    ]
    assert events[0].module == "WLAN"
    assert events[0].entities["scope"] == "WAP"
    assert events[1].level == "NOTICE"
    assert events[1].component == "dc"
    assert events[1].timestamp_raw == "2026-03-02 03:29:17.483"
    assert events[1].timestamp_normalized == "2026-03-02T03:29:17.483000"
    assert events[2].event_code == "HOSTAPD_START_FAILED"
    assert events[2].entities["error_code"] == "-22"
