"""Lossless, bounded Markdown sections and evidence-addressable method outlines."""
import hashlib
import re

from app.core.utils import mask_sensitive

MAX_DOCUMENT_CHARS = 1_000_000
SECTION_CHARS = 6000
MAX_SECTIONS = 512
DIRECTIONS = {
    "log_analysis": r"日志|log\b|错误码|error.code|event.code",
    "fault_tree": r"故障树|fault.tree|排查|诊断步骤|diagnostic.flow",
    "solution": r"解决|修复|solution|remediat|回退|rollback",
    "verification": r"验证|复发|verification|validation|recur",
    "scope": r"适用|限制|scope|limitation|固件|firmware",
    "protocol": r"协议|protocol|状态机|state.machine",
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compile_markdown(content: str) -> dict:
    if len(content) > MAX_DOCUMENT_CHARS:
        raise ValueError("Markdown exceeds the complete-analysis limit of 1,000,000 characters; split the source explicitly")
    starts = [(0, "")]
    offset = 0
    fence = None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        elif fence is None:
            heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if heading:
                if offset == 0:
                    starts[0] = (0, heading[1])
                else:
                    starts.append((offset, heading[1]))
        offset += len(line)
    sections = []
    for index, (start, heading) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(content)
        for left in range(start, end, SECTION_CHARS):
            right = min(left + SECTION_CHARS, end)
            text = content[left:right]
            identifier = f"KSEC-{left}-{right}-{digest(text)[:12]}"
            directions = [name for name, pattern in DIRECTIONS.items() if re.search(pattern, heading, re.I)]
            sections.append({"id": identifier, "start": left, "end": right,
                             "heading": mask_sensitive(heading)[:256], "sha256": digest(text),
                             "direction_hints": directions or ["unclassified"]})
    if len(sections) > MAX_SECTIONS:
        raise ValueError("Markdown has more than 512 sections; split it into explicit source documents")
    return {"compiler_version": "markdown-sections-v1", "content_sha256": digest(content),
            "characters": len(content), "covered_characters": sum(row["end"] - row["start"] for row in sections),
            "complete": True, "sections": sections, "unclosed_code_fence": fence is not None,
            "hints_are_not_model_classification": True}


def read_sections(content: str, *, content_sha256: str, offset: int = 0, limit: int = 2) -> dict:
    compiled = compile_markdown(content)
    if compiled["content_sha256"] != content_sha256:
        raise ValueError("Knowledge content changed; obtain a fresh section manifest")
    if offset < 0 or not 1 <= limit <= 2 or offset > len(compiled["sections"]):
        raise ValueError("Invalid section page")
    page = compiled["sections"][offset:offset + limit]
    next_offset = offset + len(page)
    return {"content_sha256": content_sha256, "sections": [
        {**row, "content": mask_sensitive(content[row["start"]:row["end"]])} for row in page],
        "next_offset": next_offset if next_offset < len(compiled["sections"]) else None,
        "total_sections": len(compiled["sections"]), "content_is_untrusted": True}


async def classify_complete_document(provider, context: dict, content: str, ctx) -> list[dict]:
    from app.services.knowledge_routing import classify_routing_context
    compiled = compile_markdown(content)
    source = context["documents"][0]
    votes = []
    # Pack adjacent complete sections together to avoid a model call for every heading.
    pages = []
    for section in compiled["sections"]:
        if pages and section["end"] - pages[-1][0]["start"] <= 12000:
            pages[-1].append(section)
        else:
            pages.append([section])
    for index, page in enumerate(pages):
        ctx.raise_if_cancelled()
        left, right = page[0]["start"], page[-1]["end"]
        item = {**source, "excerpt": mask_sensitive(content[left:right]), "excerpt_truncated": False,
                "markdown_outline": "\n".join(section["heading"] for section in page), "outline_truncated": False}
        decision = (await classify_routing_context(provider, {**context, "documents": [item]}))[0]
        votes.append({**decision, "section_ids": [section["id"] for section in page], "characters": right - left})
        ctx.update(35 + int(30 * (index + 1) / max(1, len(pages))), "Classifying complete Markdown sections")
    if not votes:
        raise ValueError("Markdown has no content to classify")
    weights = {}
    for vote in votes:
        weights[vote["category_id"]] = weights.get(vote["category_id"], 0) + vote["characters"] * max(0.1, vote["confidence"])
    primary = max(weights, key=weights.get)
    selected = max((row for row in votes if row["category_id"] == primary), key=lambda row: row["characters"])
    return [{**selected, "covered_section_ids": [row["id"] for row in compiled["sections"]],
             "section_classifications": votes, "mixed_directions": len(weights) > 1,
             "confidence": min(selected["confidence"], 0.65) if len(weights) > 1 else selected["confidence"],
             "complete_source_reviewed": True}]
