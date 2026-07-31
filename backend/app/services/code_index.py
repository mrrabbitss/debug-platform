import ast
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, stable_id, utcnow
from app.models import CodeRelation, CodeSymbol, Repository
from app.services.commit_graph import index_commit_graph
from app.services.jobs import JobContext
from app.services.storage import storage


SOURCE_SUFFIXES = {
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".py", ".java", ".js", ".jsx", ".ts", ".tsx", ".go",
}
IGNORE_DIRS = {
    ".git", "node_modules", "build", "dist", ".venv", "venv",
    "third_party", "vendor", "target", "__pycache__",
}
MAX_SOURCE_INDEX_BYTES = 10 * 1024 * 1024
C_FUNCTION = re.compile(
    r"(?ms)^(?P<sig>(?:[A-Za-z_][\w\s\*]*?\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*)\{"
)
C_DECLARATION = re.compile(
    r"(?m)^\s*(?P<sig>(?:[A-Za-z_][\w\s\*]*?\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\))\s*;"
)
MACRO = re.compile(r"(?m)^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)(?:\([^\n]*\))?\s*(?P<body>.*)$")
STRUCT = re.compile(
    r"(?ms)\b(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)?\s*"
    r"\{(?P<body>.*?)\}\s*(?P<alias>[A-Za-z_]\w*)?\s*;"
)
CPP_CLASS = re.compile(
    r"(?ms)\bclass\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*:\s*(?P<bases>[^{]+))?\s*\{"
)
JVM_CLASS = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+)*"
    r"(?P<kind>class|interface)\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s+extends\s+(?P<extends>[A-Za-z_$][\w.$]*))?"
    r"(?:\s+implements\s+(?P<implements>[A-Za-z0-9_$.,\s]+))?"
)
JS_FUNCTION = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)"
)
JAVA_METHOD = re.compile(
    r"(?m)^\s*(?:public|private|protected|static|final|synchronized|abstract|native|\s)+"
    r"[\w<>\[\],.?]+\s+(?P<name>[A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^{]+)?\{"
)
GO_FUNCTION = re.compile(
    r"(?m)^\s*func\s*(?:\((?P<receiver>[^)]*)\)\s*)?"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)"
)
CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
IDENTIFIER = re.compile(r"\b([A-Za-z_$][\w$]*)\b")
CONTROL_WORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "defined",
    "catch", "new", "delete", "typeof", "function", "class", "interface",
}


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
    return min(len(text), start + 30_000)


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _module_from_path(relative: str) -> str:
    parts = Path(relative).parts
    return parts[0].upper() if len(parts) > 1 else Path(relative).stem.upper()


def _calls_and_references(code: str, own_name: str) -> tuple[list[str], list[str]]:
    calls = sorted({
        name
        for name in CALL.findall(code)
        if name not in CONTROL_WORDS and name != own_name
    })
    references = sorted({
        name
        for name in IDENTIFIER.findall(code)
        if name not in CONTROL_WORDS and name != own_name and name not in set(calls)
    })
    return calls[:200], references[:500]


def _symbol(
    *,
    kind: str,
    name: str,
    relative: str,
    text: str,
    start: int,
    end: int,
    signature: str,
    calls: list[str] | None = None,
    references: list[str] | None = None,
    inherits: list[str] | None = None,
    implements: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name[:255],
        "file_path": relative,
        "line_start": _line_number(text, start),
        "line_end": _line_number(text, end),
        "signature": signature[:4000],
        "code": text[start:end][:30_000],
        "module": _module_from_path(relative),
        "calls": (calls or [])[:200],
        "references": (references or [])[:500],
        "inherits": (inherits or [])[:50],
        "implements": (implements or [])[:50],
        "metadata": metadata or {},
    }


def _extract_c_family(text: str, relative: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    definition_spans: list[tuple[int, int]] = []
    for match in C_FUNCTION.finditer(text):
        open_brace = text.find("{", match.start())
        end = _brace_end(text, open_brace)
        code = text[match.start():end]
        name = match.group("name")
        calls, references = _calls_and_references(code, name)
        symbols.append(_symbol(
            kind="function",
            name=name,
            relative=relative,
            text=text,
            start=match.start(),
            end=end,
            signature=" ".join(match.group("sig").split()),
            calls=calls,
            references=references,
        ))
        definition_spans.append((match.start(), end))
    for match in C_DECLARATION.finditer(text):
        if any(start <= match.start() < end for start, end in definition_spans):
            continue
        name = match.group("name")
        if name in CONTROL_WORDS or match.group("sig").lstrip().startswith("typedef"):
            continue
        symbols.append(_symbol(
            kind="function_declaration",
            name=name,
            relative=relative,
            text=text,
            start=match.start(),
            end=match.end(),
            signature=" ".join(match.group("sig").split()),
        ))
    for match in MACRO.finditer(text):
        calls, references = _calls_and_references(match.group(0), match.group("name"))
        symbols.append(_symbol(
            kind="macro",
            name=match.group("name"),
            relative=relative,
            text=text,
            start=match.start(),
            end=match.end(),
            signature=match.group(0),
            calls=calls,
            references=references,
        ))
    for match in STRUCT.finditer(text):
        name = match.group("name") or match.group("alias") or "anonymous_struct"
        _, references = _calls_and_references(match.group(0), name)
        symbols.append(_symbol(
            kind="struct",
            name=name,
            relative=relative,
            text=text,
            start=match.start(),
            end=match.end(),
            signature=f"struct {name}",
            references=references,
        ))
    for match in CPP_CLASS.finditer(text):
        end = _brace_end(text, text.find("{", match.start()))
        bases = []
        for raw_base in (match.group("bases") or "").split(","):
            base = re.sub(r"\b(public|protected|private|virtual)\b", "", raw_base).strip()
            if base:
                bases.append(base.split("::")[-1])
        _, references = _calls_and_references(text[match.start():end], match.group("name"))
        symbols.append(_symbol(
            kind="class",
            name=match.group("name"),
            relative=relative,
            text=text,
            start=match.start(),
            end=end,
            signature=" ".join(match.group(0).split())[:1000],
            references=references,
            inherits=bases,
        ))
    return symbols


def _ast_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_python(text: str, relative: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def span(node: ast.AST) -> tuple[int, int]:
        start_line = max(int(getattr(node, "lineno", 1)) - 1, 0)
        end_line = max(int(getattr(node, "end_lineno", start_line + 1)), start_line + 1)
        return offsets[min(start_line, len(offsets) - 1)], offsets[min(end_line, len(offsets) - 1)]

    symbols: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start, end = span(node)
        owner = None
        for candidate in ast.walk(tree):
            if isinstance(candidate, ast.ClassDef) and node in candidate.body:
                owner = candidate.name
                break
        if isinstance(node, ast.ClassDef):
            bases = [
                value
                for value in (_ast_call_name(item) for item in node.bases)
                if value
            ]
            references = sorted({
                item.id
                for item in ast.walk(node)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
            })
            symbols.append(_symbol(
                kind="class",
                name=node.name,
                relative=relative,
                text=text,
                start=start,
                end=end,
                signature=f"class {node.name}",
                references=references,
                inherits=bases,
            ))
            continue
        display_name = f"{owner}.{node.name}" if owner else node.name
        calls = sorted({
            value
            for value in (
                _ast_call_name(item.func)
                for item in ast.walk(node)
                if isinstance(item, ast.Call)
            )
            if value and value != node.name
        })
        references = sorted({
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name)
            and isinstance(item.ctx, ast.Load)
            and item.id not in set(calls)
            and item.id != node.name
        })
        kind = "method" if owner else "function"
        symbols.append(_symbol(
            kind=kind,
            name=display_name,
            relative=relative,
            text=text,
            start=start,
            end=end,
            signature=f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {display_name}",
            calls=calls,
            references=references,
            metadata={"owner": owner} if owner else {},
        ))
    return symbols


def _extract_java_script_typescript(text: str, relative: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for match in JVM_CLASS.finditer(text):
        open_brace = text.find("{", match.end())
        end = _brace_end(text, open_brace) if open_brace >= 0 else match.end()
        extends = [match.group("extends")] if match.group("extends") else []
        implements = [
            item.strip()
            for item in (match.group("implements") or "").split(",")
            if item.strip()
        ]
        _, references = _calls_and_references(text[match.start():end], match.group("name"))
        symbols.append(_symbol(
            kind=match.group("kind"),
            name=match.group("name"),
            relative=relative,
            text=text,
            start=match.start(),
            end=end,
            signature=" ".join(match.group(0).split()),
            references=references,
            inherits=extends,
            implements=implements,
        ))
    patterns = [JS_FUNCTION]
    if Path(relative).suffix.lower() == ".java":
        patterns.append(JAVA_METHOD)
    for pattern in patterns:
        for match in pattern.finditer(text):
            open_brace = text.find("{", match.end() - 1)
            end = _brace_end(text, open_brace) if open_brace >= 0 else match.end()
            code = text[match.start():end]
            calls, references = _calls_and_references(code, match.group("name"))
            symbols.append(_symbol(
                kind="function" if pattern is JS_FUNCTION else "method",
                name=match.group("name"),
                relative=relative,
                text=text,
                start=match.start(),
                end=end,
                signature=" ".join(match.group(0).split()),
                calls=calls,
                references=references,
            ))
    return symbols


def _extract_go(text: str, relative: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for match in GO_FUNCTION.finditer(text):
        open_brace = text.find("{", match.end())
        end = _brace_end(text, open_brace) if open_brace >= 0 else match.end()
        receiver = (match.group("receiver") or "").strip()
        name = f"{receiver.split()[-1].lstrip('*')}.{match.group('name')}" if receiver else match.group("name")
        calls, references = _calls_and_references(text[match.start():end], match.group("name"))
        symbols.append(_symbol(
            kind="method" if receiver else "function",
            name=name,
            relative=relative,
            text=text,
            start=match.start(),
            end=end,
            signature=" ".join(match.group(0).split()),
            calls=calls,
            references=references,
            metadata={"receiver": receiver} if receiver else {},
        ))
    return symbols


def extract_symbols(path: Path, root: Path) -> list[dict[str, Any]]:
    relative = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = path.suffix.lower()
    if suffix in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp"}:
        return _extract_c_family(text, relative)
    if suffix == ".py":
        return _extract_python(text, relative)
    if suffix in {".java", ".js", ".jsx", ".ts", ".tsx"}:
        return _extract_java_script_typescript(text, relative)
    if suffix == ".go":
        return _extract_go(text, relative)
    return []


def _short_name(name: str) -> str:
    return name.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _choose_target(source: CodeSymbol, candidates: list[CodeSymbol]) -> CodeSymbol | None:
    candidates = [item for item in candidates if item.id != source.id]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            item.file_path != source.file_path,
            item.module != source.module,
            item.kind in {"function_declaration"},
            item.file_path,
            item.line_start,
        ),
    )[0]


def _build_code_relations(repository_id: str, generation_id: str) -> int:
    with SessionLocal() as db:
        symbols = list(db.scalars(
            select(CodeSymbol).where(
                CodeSymbol.repository_id == repository_id,
                CodeSymbol.generation_id == generation_id,
            )
        ).all())
        exact: dict[str, list[CodeSymbol]] = defaultdict(list)
        short: dict[str, list[CodeSymbol]] = defaultdict(list)
        for symbol in symbols:
            exact[symbol.name].append(symbol)
            short[_short_name(symbol.name)].append(symbol)
        relation_count = 0
        batch: list[CodeRelation] = []
        for source in symbols:
            metadata = json_loads(source.metadata_json, {})
            relation_specs: list[tuple[str, str, float]] = []
            relation_specs.extend(
                ("CALLS", name, 0.9) for name in json_loads(source.calls_json, [])
            )
            relation_specs.extend(
                ("REFERENCES", name, 0.65) for name in metadata.get("references", [])
            )
            relation_specs.extend(
                ("INHERITS", name, 0.98) for name in metadata.get("inherits", [])
            )
            relation_specs.extend(
                ("IMPLEMENTS", name, 0.98) for name in metadata.get("implements", [])
            )
            if source.kind in {"function", "method"}:
                declaration_targets = [
                    item
                    for item in short.get(_short_name(source.name), [])
                    if item.kind == "function_declaration"
                ]
                for declaration in declaration_targets[:5]:
                    relation_specs.append(("IMPLEMENTS", declaration.name, 0.95))

            seen: set[tuple[str, str]] = set()
            for relation_type, target_name, confidence in relation_specs:
                normalized_name = _short_name(str(target_name).strip())
                key = (relation_type, normalized_name)
                if not normalized_name or key in seen or len(seen) >= 250:
                    continue
                seen.add(key)
                candidates = exact.get(str(target_name), []) or short.get(normalized_name, [])
                if relation_type == "IMPLEMENTS" and source.kind in {"function", "method"}:
                    declarations = [
                        item for item in candidates if item.kind == "function_declaration"
                    ]
                    candidates = declarations or candidates
                target = _choose_target(source, candidates)
                if relation_type == "REFERENCES" and target is None:
                    continue
                source_logical_id = source.logical_id or source.id
                target_logical_id = (
                    (target.logical_id or target.id)
                    if target
                    else ""
                )
                logical_id = stable_id(
                    "REL",
                    repository_id,
                    source_logical_id,
                    relation_type,
                    normalized_name,
                    target_logical_id,
                )
                batch.append(CodeRelation(
                    id=stable_id(
                        "RELREV",
                        logical_id,
                        generation_id,
                    ),
                    logical_id=logical_id,
                    repository_id=repository_id,
                    generation_id=generation_id,
                    source_symbol_id=source.id,
                    target_symbol_id=target.id if target else None,
                    target_name=str(target_name)[:512],
                    relation_type=relation_type,
                    confidence=confidence if target else min(confidence, 0.45),
                    evidence_json=json_dumps({
                        "source_file": source.file_path,
                        "source_line": source.line_start,
                        "source_evidence_id": source_logical_id,
                        "target_evidence_id": target_logical_id or None,
                        "resolved": target is not None,
                    }),
                ))
                relation_count += 1
                if len(batch) >= 1000:
                    db.add_all(batch)
                    db.commit()
                    batch = []
        if batch:
            db.add_all(batch)
            db.commit()
        return relation_count


def _index_repository_impl(
    ctx: JobContext,
    repository_id: str,
    *,
    generation_id: str | None = None,
) -> dict[str, Any]:
    generation_id = generation_id or new_id("CGEN")
    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if not repository:
            raise ValueError("Repository not found")
        previous_generation = repository.active_graph_generation_id
        repository.status = "INDEXING"
        repository.graph_status = "INDEXING"
        repository.commit_graph_status = "INDEXING"
        metadata = json_loads(repository.index_metadata_json, {})
        metadata["building_graph_generation_id"] = generation_id
        repository.index_metadata_json = json_dumps(metadata)
        db.commit()
        root = storage.resolve_path(repository.root_path)
        if not root.is_dir():
            raise ValueError("Repository root directory was not found")

    files: list[Path] = []
    skipped_oversized = 0
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.suffix.lower() not in SOURCE_SUFFIXES
            or any(part in IGNORE_DIRS for part in path.relative_to(root).parts)
        ):
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_INDEX_BYTES:
                skipped_oversized += 1
                continue
        except OSError:
            continue
        files.append(path)
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    symbol_count = 0
    pending: list[CodeSymbol] = []
    symbol_occurrences: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for index, path in enumerate(files):
        for symbol in extract_symbols(path, root):
            identity = (
                symbol["kind"],
                symbol["name"],
                symbol["file_path"],
                symbol["signature"],
            )
            occurrence = symbol_occurrences[identity]
            symbol_occurrences[identity] += 1
            logical_id = stable_id(
                "SYM",
                repository_id,
                *identity,
                occurrence,
            )
            pending.append(CodeSymbol(
                id=stable_id(
                    "SYMREV",
                    logical_id,
                    generation_id,
                ),
                repository_id=repository_id,
                generation_id=generation_id,
                logical_id=logical_id,
                kind=symbol["kind"],
                name=symbol["name"],
                file_path=symbol["file_path"],
                line_start=symbol["line_start"],
                line_end=symbol["line_end"],
                signature=symbol["signature"],
                code=symbol["code"],
                module=symbol["module"],
                calls_json=json_dumps(symbol["calls"]),
                metadata_json=json_dumps({
                    **symbol["metadata"],
                    "references": symbol["references"],
                    "inherits": symbol["inherits"],
                    "implements": symbol["implements"],
                }),
            ))
            symbol_count += 1
        if len(pending) >= 500 or index == len(files) - 1:
            with SessionLocal() as db:
                db.add_all(pending)
                db.commit()
            pending = []
        if index % 10 == 0:
            ctx.update(
                5 + int(65 * (index + 1) / max(len(files), 1)),
                f"Indexed source files: {index + 1}/{len(files)}",
            )

    ctx.update(72, "Building call, reference, inheritance and implementation edges")
    relation_count = _build_code_relations(repository_id, generation_id)
    with SessionLocal() as db:
        expected_generation = (
            Repository.active_graph_generation_id.is_(None)
            if previous_generation is None
            else Repository.active_graph_generation_id
            == previous_generation
        )
        published = db.execute(
            update(Repository)
            .where(
                Repository.id == repository_id,
                expected_generation,
            )
            .values(
                active_graph_generation_id=generation_id,
                graph_status="INDEXED",
            )
        )
        if published.rowcount != 1:
            db.rollback()
            raise RuntimeError(
                "Code graph build was superseded by another generation"
            )
        repository = db.get(Repository, repository_id)
        if not repository:
            db.rollback()
            raise ValueError("Repository was deleted during indexing")
        metadata = json_loads(repository.index_metadata_json, {})
        if metadata.get("building_graph_generation_id") == generation_id:
            metadata.pop("building_graph_generation_id", None)
        metadata["code_graph"] = {
            "status": "INDEXED",
            "generation_id": generation_id,
            "previous_generation_id": previous_generation,
            "files_scanned": len(files),
            "files_skipped_oversized": skipped_oversized,
            "max_source_file_bytes": MAX_SOURCE_INDEX_BYTES,
            "symbols": symbol_count,
            "relations": relation_count,
            "relation_types": ["CALLS", "REFERENCES", "INHERITS", "IMPLEMENTS"],
        }
        repository.index_metadata_json = json_dumps(metadata)
        db.commit()
        db.execute(delete(CodeRelation).where(
            CodeRelation.repository_id == repository_id,
            CodeRelation.generation_id != generation_id,
        ))
        db.execute(delete(CodeSymbol).where(
            CodeSymbol.repository_id == repository_id,
            CodeSymbol.generation_id != generation_id,
        ))
        db.commit()

    ctx.update(88, "Reading Git history and linking commits to code")
    commit_result = index_commit_graph(repository_id, root)
    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if repository:
            repository.status = "INDEXED"
            repository.indexed_at = utcnow()
            db.commit()
    return {
        "repository_id": repository_id,
        "graph_generation_id": generation_id,
        "files_scanned": len(files),
        "files_skipped_oversized": skipped_oversized,
        "symbols": symbol_count,
        "relations": relation_count,
        "commit_graph": commit_result,
    }


def index_repository_job(ctx: JobContext, repository_id: str) -> dict[str, Any]:
    generation_id = new_id("CGEN")
    try:
        return _index_repository_impl(
            ctx,
            repository_id,
            generation_id=generation_id,
        )
    except Exception:
        with SessionLocal() as db:
            repository = db.get(Repository, repository_id)
            if repository:
                metadata = json_loads(repository.index_metadata_json, {})
                if (
                    metadata.get("building_graph_generation_id")
                    == generation_id
                ):
                    metadata.pop("building_graph_generation_id", None)
                if generation_id != repository.active_graph_generation_id:
                    db.execute(delete(CodeRelation).where(
                        CodeRelation.repository_id == repository_id,
                        CodeRelation.generation_id == generation_id,
                    ))
                    db.execute(delete(CodeSymbol).where(
                        CodeSymbol.repository_id == repository_id,
                        CodeSymbol.generation_id == generation_id,
                    ))
                metadata["last_index_error_at"] = utcnow().isoformat()
                repository.index_metadata_json = json_dumps(metadata)
                repository.status = "INDEX_FAILED"
                if repository.graph_status == "INDEXING":
                    repository.graph_status = (
                        "INDEXED"
                        if repository.active_graph_generation_id
                        else "INDEX_FAILED"
                    )
                if repository.commit_graph_status == "INDEXING":
                    repository.commit_graph_status = "INDEX_FAILED"
                db.commit()
        raise
