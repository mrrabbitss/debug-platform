import subprocess
import sys
from pathlib import Path


def test_parse_service_registers_builtin_parsers_in_fresh_process():
    backend_root = Path(__file__).resolve().parents[1]
    script = (
        "from pathlib import Path; "
        "from app.services.parse_service import registry; "
        "parser_ids = {parser.parser_id for parser in registry.parsers}; "
        "assert {'generic-log', 'huawei-collectdebuginfo', 'json-line'} <= parser_ids, parser_ids; "
        "parser = registry.select("
        "Path('001122334455_GW_collectDebuginfo_2026_03_02_11_22_490.txt'), "
        "'Start run collect command:WAP:get wlan basic laninst 1 wlaninst6'); "
        "assert parser.parser_id == 'huawei-collectdebuginfo', parser.parser_id"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
