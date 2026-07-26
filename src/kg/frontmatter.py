from __future__ import annotations
import re
from dataclasses import dataclass, asdict
import yaml

_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class RawFrontmatter:
    source: str
    sha256: str
    type: str
    title: str
    ingested_at: str
    chunks: list[dict]


def render(fm: RawFrontmatter, body: str) -> str:
    payload = asdict(fm)
    yaml_block = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_block}\n---\n{body}"


def parse(text: str) -> tuple[RawFrontmatter, str]:
    m = _FM.match(text)
    if not m:
        raise ValueError("Missing YAML frontmatter block.")
    data = yaml.safe_load(m.group(1))
    fm = RawFrontmatter(
        source=data["source"],
        sha256=data["sha256"],
        type=data["type"],
        title=data.get("title", ""),
        ingested_at=data["ingested_at"],
        chunks=data.get("chunks", []),
    )
    return fm, m.group(2)
