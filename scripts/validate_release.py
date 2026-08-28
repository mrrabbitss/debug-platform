#!/usr/bin/env python3
"""Validate the Skill-only release envelope without third-party packages."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    try:
        body = text.split("---\n", 2)[1]
    except IndexError:
        return {}
    result: dict[str, str] = {}
    in_metadata = False
    for line in body.splitlines():
        if line == "metadata:":
            in_metadata = True
            continue
        match = re.match(r"^(\s*)([A-Za-z_][\w-]*):\s*[\"']?(.*?)[\"']?\s*$", line)
        if not match:
            continue
        indent, key, value = match.groups()
        if in_metadata and indent:
            result[f"metadata.{key}"] = value
        elif not indent:
            in_metadata = False
            result[key] = value
    return result


def local_markdown_links(path: Path) -> list[Path]:
    missing: list[Path] = []
    for raw_target in LINK_RE.findall(path.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
        if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        resolved = (path.parent / unquote(target)).resolve()
        if not resolved.exists():
            missing.append(resolved)
    return missing


def main() -> int:
    checks: list[str] = []
    failures: list[str] = []

    def require(condition: bool, success: str, failure: str) -> None:
        if condition:
            checks.append(success)
        else:
            failures.append(failure)

    skill_path = ROOT / "SKILL.md"
    manifest_path = ROOT / "skill-manifest.json"
    require(skill_path.is_file(), "SKILL.md exists", "SKILL.md is missing")
    require(manifest_path.is_file(), "skill-manifest.json exists", "skill-manifest.json is missing")
    if not skill_path.is_file() or not manifest_path.is_file():
        print(json.dumps({"ok": False, "checks": checks, "failures": failures}, indent=2))
        return 2

    skill_text = skill_path.read_text(encoding="utf-8")
    metadata = frontmatter(skill_text)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version", ""))
    description = metadata.get("description", "")

    require(metadata.get("name") == "gw-ap-debug", "Skill name is canonical", "Skill name must be gw-ap-debug")
    require(
        len(description) >= 100 and "GW/AP" in description and "generic" in description,
        "Skill description is discriminating",
        "Skill description must clearly distinguish GW/AP use from generic debugging",
    )
    require(bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), "Manifest version is semantic", "Manifest version is not x.y.z")
    require(metadata.get("metadata.version") == version, "Skill and manifest versions match", "SKILL.md and manifest versions differ")
    require(manifest.get("name") == "gw-ap-debug", "Manifest name is canonical", "Manifest name must be gw-ap-debug")
    require(manifest.get("python") == ">=3.11,<3.15", "Python compatibility is explicit", "Python compatibility drifted")
    require(
        manifest.get("distribution_status") == "PUBLIC_RELEASE_WITH_BUNDLED_METHODS_BY_OWNER_DIRECTION",
        "Distribution decision is preserved",
        "Distribution decision is missing or changed",
    )

    required_files = [
        "DEPLOYMENT.md",
        "VALIDATION.md",
        ".gitattributes",
        ".github/workflows/skillonly-validation.yml",
        "scripts/debug_platform_skill.py",
        "scripts/check_provenance.py",
        "scripts/setup_user_skill.ps1",
        "scripts/setup_user_skill.sh",
        "references/installation.md",
        "references/host-agent-mode.md",
        "references/capability-scope.md",
        "references/security-and-distribution.md",
    ]
    for relative in required_files:
        require((ROOT / relative).is_file(), f"Required file exists: {relative}", f"Required file is missing: {relative}")

    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8") if (ROOT / ".gitattributes").is_file() else ""
    require("* -text" in attrs, "Runtime byte-preservation rule exists", ".gitattributes must preserve runtime bytes")
    require("[DEPLOYMENT.md](DEPLOYMENT.md)" in skill_text, "SKILL.md routes to deployment guide", "SKILL.md must link DEPLOYMENT.md")

    docs = [skill_path, ROOT / "DEPLOYMENT.md"] + sorted((ROOT / "references").glob("*.md"))
    missing_links: list[str] = []
    for document in docs:
        if document.is_file():
            missing_links.extend(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path) for path in local_markdown_links(document))
    require(not missing_links, "All local documentation links resolve", f"Broken local links: {', '.join(missing_links)}")

    result = {"ok": not failures, "version": version, "checks": checks, "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
