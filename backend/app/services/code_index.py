import re
from pathlib import Path

from sqlalchemy import delete

from app.core.db import SessionLocal
from app.core.utils import json_dumps, new_id
from app.models import CodeSymbol, Repository
from app.services.jobs import JobContext
from app.services.storage import storage


SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".hpp", ".mk", ".cmake", ".py", ".sh"}
IGNORE_DIRS = {".git", "node_modules", "build", "dist", ".venv", "venv", "third_party", "vendor"}
C_FUNCTION = re.compile(
    r"(?ms)^(?P<sig>(?:[A-Za-z_][\w\s\*]*?\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*)\{"
)
MACRO = re.compile(r"(?m)^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)(?:\([^\n]*\))?\s*(?P<body>.*)$")
STRUCT = re.compile(r"(?ms)\b(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)?\s*\{(?P<body>.*?)\}\s*(?P<alias>[A-Za-z_]\w*)?\s*;")
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
CONTROL_WORDS = {"if", "for", "while", "switch", "return", "sizeof", "defined"}


def _brace_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return min(len(text), start + 8000)


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _module_from_path(relative: str) -> str:
    parts = Path(relative).parts
    return parts[0].upper() if len(parts) > 1 else Path(relative).stem.upper()


def extract_symbols(path: Path, root: Path) -> list[dict]:
    relative = str(path.relative_to(root)).replace("\\", "/")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    symbols: list[dict] = []
    if path.suffix.lower() in {".c", ".h", ".cc", ".cpp", ".hpp"}:
        for match in C_FUNCTION.finditer(text):
            open_brace = text.find("{", match.start())
            end = _brace_end(text, open_brace)
            code = text[match.start():end]
            calls = sorted({name for name in CALL.findall(code) if name not in CONTROL_WORDS and name != match.group("name")})
            symbols.append({
                "kind": "function", "name": match.group("name"), "file_path": relative,
                "line_start": _line_number(text, match.start()), "line_end": _line_number(text, end),
                "signature": " ".join(match.group("sig").split()), "code": code[:30000],
                "module": _module_from_path(relative), "calls": calls,
            })
        for match in MACRO.finditer(text):
            symbols.append({
                "kind": "macro", "name": match.group("name"), "file_path": relative,
                "line_start": _line_number(text, match.start()), "line_end": _line_number(text, match.end()),
                "signature": match.group(0)[:500], "code": match.group(0)[:5000],
                "module": _module_from_path(relative), "calls": [],
            })
        for match in STRUCT.finditer(text):
            name = match.group("name") or match.group("alias") or "anonymous_struct"
            symbols.append({
                "kind": "struct", "name": name, "file_path": relative,
                "line_start": _line_number(text, match.start()), "line_end": _line_number(text, match.end()),
                "signature": f"struct {name}", "code": match.group(0)[:30000],
                "module": _module_from_path(relative), "calls": [],
            })
    return symbols


def _index_repository_impl(ctx: JobContext, repository_id: str) -> dict:
    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if not repository:
            raise ValueError("Repository not found")
        repository.status = "INDEXING"
        db.execute(delete(CodeSymbol).where(CodeSymbol.repository_id == repository_id))
        db.commit()
        root = storage.resolve_path(repository.root_path)

    files = [
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES and not any(part in IGNORE_DIRS for part in path.parts)
    ]
    count = 0
    for idx, path in enumerate(files):
        for symbol in extract_symbols(path, root):
            with SessionLocal() as db:
                db.add(CodeSymbol(
                    id=new_id("SYM"), repository_id=repository_id,
                    kind=symbol["kind"], name=symbol["name"], file_path=symbol["file_path"],
                    line_start=symbol["line_start"], line_end=symbol["line_end"],
                    signature=symbol["signature"], code=symbol["code"], module=symbol["module"],
                    calls_json=json_dumps(symbol["calls"]), metadata_json="{}",
                ))
                db.commit()
            count += 1
        if idx % 5 == 0:
            ctx.update(5 + int(90 * (idx + 1) / max(len(files), 1)), f"Indexed {path.name}")

    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if repository:
            repository.status = "INDEXED"
            db.commit()
    return {"repository_id": repository_id, "files_scanned": len(files), "symbols": count}


def index_repository_job(ctx: JobContext, repository_id: str) -> dict:
    try:
        return _index_repository_impl(ctx, repository_id)
    except Exception:
        with SessionLocal() as db:
            repository = db.get(Repository, repository_id)
            if repository:
                repository.status = "INDEX_FAILED"
                db.commit()
        raise
