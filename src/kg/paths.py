from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from kg import KgError


@dataclass(frozen=True)
class KgPaths:
    root: Path

    @classmethod
    def for_root(cls, root: Path) -> "KgPaths":
        return cls(root=Path(root))

    @classmethod
    def for_cwd(cls, cwd: Path | None = None) -> "KgPaths":
        here = Path(cwd) if cwd else Path.cwd()
        kg_dir = here / ".kg"
        if not kg_dir.is_dir():
            raise KgError(
                f"No .kg/ found in {here}. Run `kg init` first."
            )
        return cls.for_root(kg_dir)

    @property
    def raw(self) -> Path:            return self.root / "raw"
    @property
    def raw_conversations(self) -> Path: return self.root / "raw" / "conversations"
    @property
    def wiki(self) -> Path:           return self.root / "wiki"
    @property
    def wiki_index(self) -> Path:     return self.wiki / "index.md"
    @property
    def registry(self) -> Path:       return self.root / "registry.jsonl"
    @property
    def ontology(self) -> Path:       return self.root / "ontology.json"
    @property
    def config(self) -> Path:         return self.root / "config.toml"
    @property
    def snapshots(self) -> Path:      return self.root / "snapshots"
    @property
    def review(self) -> Path:         return self.root / "review"
    @property
    def kg_db(self) -> Path:          return self.root / "kg.db"

    def ensure(self) -> None:
        for d in (self.raw, self.raw_conversations, self.wiki,
                  self.snapshots, self.review):
            d.mkdir(parents=True, exist_ok=True)
