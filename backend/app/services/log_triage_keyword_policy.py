from __future__ import annotations

import re
from typing import Any


_GENERIC_KEYWORDS = {
    "ap", "critical", "debug", "error", "failed", "failure", "gw", "info",
    "notice", "offline", "online", "start", "status", "stop", "success", "warn",
    "warning",
}
_PRECISE_MARKER = re.compile(r"[0-9\[\]():=./_-]")
_CAMEL_CASE = re.compile(r"[a-z][A-Z]|[A-Z][a-z]+[A-Z]")


def is_precise_additional_keyword(keyword: str) -> bool:
    """Reject broad model terms that would promote unrelated log lines."""

    stripped = keyword.strip()
    if stripped.casefold() in _GENERIC_KEYWORDS or len(stripped) < 4:
        return False
    if _PRECISE_MARKER.search(stripped) or _CAMEL_CASE.search(stripped):
        return True
    if any("\u4e00" <= char <= "\u9fff" for char in stripped):
        return len(stripped) >= 4
    return len(stripped) >= 8


def filter_additional_keyword_proposals(
    proposals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected = 0
    for proposal in proposals:
        keyword = str(proposal.get("keyword") or "")
        normalized = keyword.strip().casefold()
        if normalized in seen or not is_precise_additional_keyword(keyword):
            rejected += 1
            continue
        seen.add(normalized)
        accepted.append(proposal)
    return accepted, rejected
