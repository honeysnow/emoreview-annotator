"""Loads codebook.yaml."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "codebook.yaml"


@dataclass(frozen=True)
class Codebook:
    version: int
    tags: tuple[str, ...]
    definitions: dict[str, str]
    section2_tags: frozenset[str]
    polarities: tuple[str, ...]
    arousal: dict[int, str]
    sources: tuple[str, ...]

    def is_section2(self, tag: str) -> bool:
        return tag in self.section2_tags


@lru_cache(maxsize=None)
def load_codebook(path: Path | str = DEFAULT_PATH) -> Codebook:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    tags = tuple(t["name"] for t in data["tags"])
    section2 = frozenset(data["section2_tags"])
    if len(tags) != 30:
        raise ValueError(f"codebook must define 30 tags, found {len(tags)}")
    if not section2 <= set(tags):
        raise ValueError("section2_tags must be a subset of tags")
    return Codebook(
        version=data["version"],
        tags=tags,
        definitions={t["name"]: t.get("definition", "") for t in data["tags"]},
        section2_tags=section2,
        polarities=tuple(data["polarities"]),
        arousal={int(k): v for k, v in data["arousal"].items()},
        sources=tuple(data["sources"]),
    )
