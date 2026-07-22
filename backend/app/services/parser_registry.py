from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable, Iterator
from typing import Protocol


@dataclass
class ParsedEvent:
    source_file: str
    line_start: int
    line_end: int
    timestamp_raw: str | None
    timestamp_normalized: str | None
    level: str
    module: str
    component: str
    event_code: str
    message: str
    raw_text: str
    entities: dict = field(default_factory=dict)
    parser_id: str = "generic"
    parser_version: str = "1.0"
    confidence: float = 0.5


class Parser(Protocol):
    parser_id: str
    parser_version: str

    def probe(self, path: Path, sample: str) -> float: ...

    def parse(self, path: Path, relative_path: str, text: str) -> list[ParsedEvent]: ...

    def parse_lines(
        self,
        path: Path,
        relative_path: str,
        lines: Iterable[str],
        sample: str = "",
    ) -> Iterator[ParsedEvent]: ...


class ParserRegistry:
    def __init__(self) -> None:
        self.parsers: list[Parser] = []

    def register(self, parser: Parser) -> None:
        self.parsers.append(parser)

    def select(self, path: Path, sample: str) -> Parser:
        if not self.parsers:
            raise RuntimeError("No parsers registered")
        scored = sorted(((p.probe(path, sample), p) for p in self.parsers), key=lambda item: item[0], reverse=True)
        return scored[0][1]


registry = ParserRegistry()
