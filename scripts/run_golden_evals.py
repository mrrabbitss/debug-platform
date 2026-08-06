"""Run deterministic golden quality gates and emit JSON/JUnit artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.harness.golden import run_golden_suite  # noqa: E402


def _write_junit(report: dict, path: Path) -> None:
    suite = ET.Element(
        "testsuite",
        name="golden-dataset",
        tests=str(report["check_count"]),
        failures=str(sum(1 for item in report["checks"] if item["status"] == "FAIL")),
        time=str(round(sum(float(item["duration_ms"]) for item in report["checks"]) / 1000, 3)),
    )
    for check in report["checks"]:
        case = ET.SubElement(
            suite,
            "testcase",
            classname="harness.golden",
            name=check["name"],
            time=str(round(float(check["duration_ms"]) / 1000, 3)),
        )
        if check["failures"]:
            failure = ET.SubElement(case, "failure", message=check["failures"][0])
            failure.text = "\n".join(check["failures"])
        output = ET.SubElement(case, "system-out")
        output.text = json.dumps(check["metrics"], ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "sample_data" / "golden_incident",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    report = run_golden_suite(args.corpus)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.junit:
        _write_junit(report, args.junit)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
