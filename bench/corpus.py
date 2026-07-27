"""Deterministic benchmark corpus generator.

Produces markdown files with POLE entities (persons, orgs, locations, events),
cross-document references, gray-zone near-duplicate name pairs, and facts/preferences.
Seeded RNG ensures byte-identical re-runs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

# POLE entity pools — fixed, no randomness at module level
_PERSONS = [
    ("Elena Vasquez", "person"),
    ("Marcus Chen", "person"),
    ("Aisha Patel", "person"),
    ("James O'Brien", "person"),
    ("Sofia Magnusson", "person"),
    ("Raj Krishnamurthy", "person"),
    ("Lena Hoffmann", "person"),
    ("Tomás Rivera", "person"),
    ("Yuki Tanaka", "person"),
    ("Paris", "person"),  # gray-zone: same name as city below
    ("Amara Okafor", "person"),
    ("Viktor Novak", "person"),
    ("Priya Sharma", "person"),
    ("Diego Morales", "person"),
    ("Fatima Al-Rashid", "person"),
]

_ORGS = [
    ("Meridian Labs", "organization"),
    ("Cascade AI", "organization"),
    ("Northwind Research", "organization"),
    ("Vertex Dynamics", "organization"),
    ("Solaris Foundation", "organization"),
    ("Pinnacle Corp", "organization"),
    ("Horizon Biotech", "organization"),
    ("Atlas Computing", "organization"),
    ("Quantum Leap Inc", "organization"),
    ("Ember Studios", "organization"),
    ("Zenith Labs", "organization"),
    ("Lumina Health", "organization"),
]

_LOCATIONS = [
    ("Berlin", "location"),
    ("Tokyo", "location"),
    ("São Paulo", "location"),
    ("Reykjavik", "location"),
    ("Nairobi", "location"),
    ("Paris", "location"),  # gray-zone: same name as person above
    ("Mumbai", "location"),
    ("Vancouver", "location"),
    ("Stockholm", "location"),
    ("Singapore", "location"),
    ("Cape Town", "location"),
    ("Austin", "location"),
]

_EVENTS = [
    ("DataSummit 2024", "event"),
    ("NeurIPS 2023", "event"),
    ("Open Source Summit", "event"),
    ("Climate Action Forum", "event"),
    ("HealthTech Conference", "event"),
    ("Quantum Computing Workshop", "event"),
    ("AI Ethics Symposium", "event"),
    ("Global Health Summit", "event"),
]

_FACTS = [
    ("Meridian Labs was founded in 2018.", "fact"),
    ("Cascade AI uses transformer-based models for document analysis.", "fact"),
    ("Berlin has a population of approximately 3.8 million.", "fact"),
    ("DataSummit 2024 was held in Tokyo.", "fact"),
    ("Northwind Research specializes in climate modeling.", "fact"),
    ("São Paulo is the largest city in South America by population.", "fact"),
    ("Reykjavik runs almost entirely on geothermal energy.", "fact"),
    ("Vertex Dynamics was acquired by Pinnacle Corp in 2022.", "fact"),
    ("Nairobi is a major tech hub in East Africa.", "fact"),
    ("Elena Vasquez won the Turing Award in 2023.", "fact"),
    ("Marcus Chen published 47 papers on graph neural networks.", "fact"),
    ("Solaris Foundation funds renewable energy research.", "fact"),
    ("Horizon Biotech developed a rapid diagnostic test for malaria.", "fact"),
    ("Amara Okafor leads the Nairobi AI research center.", "fact"),
    ("Yuki Tanaka presented at NeurIPS 2023.", "fact"),
]

_PREFERENCES = [
    ("The team prefers Python for prototyping and Rust for production services.", "preference"),
    ("Open-source licenses (MIT, Apache 2.0) are preferred over proprietary ones.", "preference"),
    ("All research data should be stored in open formats (Parquet, CSV).", "preference"),
    ("Code reviews are required before merging to main.", "preference"),
    ("Documentation should live next to the code it describes.", "preference"),
    ("Meetings should not exceed 30 minutes unless explicitly scheduled longer.", "preference"),
]

# Template slots for document generation
_DOC_TEMPLATES = [
    # (title_prefix, sections_fn_name)
    ("Biography", "_bio_sections"),
    ("Technical Report", "_tech_sections"),
    ("Meeting Notes", "_meeting_sections"),
    ("Project Overview", "_project_sections"),
    ("Research Summary", "_research_sections"),
]


def _bio_sections(
    person: tuple[str, str],
    org: tuple[str, str],
    location: tuple[str, str],
    event: tuple[str, str],
    fact: tuple[str, str],
    preference: tuple[str, str],
    cross_ref_person: tuple[str, str],
) -> list[str]:
    name = person[0]
    return [
        f"# Biography: {name}",
        "",
        f"{name} is a researcher at {org[0]}, based in {location[0]}. "
        f"They attended {event[0]} and presented their work on knowledge graphs.",
        "",
        f"## Background",
        "",
        f"{name} has extensive experience in data science and machine learning. "
        f"They frequently collaborate with {cross_ref_person[0]} on cross-document analysis projects.",
        "",
        f"## Key Fact",
        "",
        fact[0],
        "",
        f"## Team Preferences",
        "",
        preference[0],
    ]


def _tech_sections(
    person: tuple[str, str],
    org: tuple[str, str],
    location: tuple[str, str],
    event: tuple[str, str],
    fact: tuple[str, str],
    preference: tuple[str, str],
    cross_ref_person: tuple[str, str],
) -> list[str]:
    return [
        f"# Technical Report: {org[0]}",
        "",
        f"{org[0]} is headquartered in {location[0]}. The team is led by {person[0]}.",
        "",
        f"## Research Focus",
        "",
        f"The primary focus is on building scalable knowledge graph systems. "
        f"{cross_ref_person[0]} contributed to the initial architecture design.",
        "",
        f"## Conference Presentation",
        "",
        f"Results were presented at {event[0]}.",
        "",
        f"## Technical Detail",
        "",
        fact[0],
        "",
        f"## Engineering Standards",
        "",
        preference[0],
    ]


def _meeting_sections(
    person: tuple[str, str],
    org: tuple[str, str],
    location: tuple[str, str],
    event: tuple[str, str],
    fact: tuple[str, str],
    preference: tuple[str, str],
    cross_ref_person: tuple[str, str],
) -> list[str]:
    return [
        f"# Meeting Notes — {org[0]} Planning",
        "",
        f"Attendees: {person[0]}, {cross_ref_person[0]}",
        f"Location: {location[0]}",
        f"Date: 2024-03-15",
        "",
        f"## Agenda",
        "",
        f"1. Discuss preparations for {event[0]}",
        f"2. Review collaboration with {cross_ref_person[0]}'s team",
        f"3. Engineering workflow updates",
        "",
        f"## Notes",
        "",
        fact[0],
        "",
        f"## Action Items",
        "",
        preference[0],
    ]


def _project_sections(
    person: tuple[str, str],
    org: tuple[str, str],
    location: tuple[str, str],
    event: tuple[str, str],
    fact: tuple[str, str],
    preference: tuple[str, str],
    cross_ref_person: tuple[str, str],
) -> list[str]:
    return [
        f"# Project: {org[0]} Knowledge Platform",
        "",
        f"{person[0]} leads this initiative from {location[0]}. "
        f"The project collaborates with {cross_ref_person[0]}'s research group.",
        "",
        f"## Milestone",
        "",
        f"The team presented at {event[0]}.",
        "",
        f"## Technical Details",
        "",
        fact[0],
        "",
        f"## Development Guidelines",
        "",
        preference[0],
    ]


def _research_sections(
    person: tuple[str, str],
    org: tuple[str, str],
    location: tuple[str, str],
    event: tuple[str, str],
    fact: tuple[str, str],
    preference: tuple[str, str],
    cross_ref_person: tuple[str, str],
) -> list[str]:
    return [
        f"# Research Summary — {person[0]}",
        "",
        f"{person[0]} works at {org[0]} in {location[0]}. "
        f"Key collaborators include {cross_ref_person[0]}.",
        "",
        f"## Conference",
        "",
        f"Presented findings at {event[0]}.",
        "",
        f"## Finding",
        "",
        fact[0],
        "",
        f"## Methodology Preferences",
        "",
        preference[0],
    ]


_SECTION_FNS = {
    "_bio_sections": _bio_sections,
    "_tech_sections": _tech_sections,
    "_meeting_sections": _meeting_sections,
    "_project_sections": _project_sections,
    "_research_sections": _research_sections,
}


def _stable_hash(parts: list[str], *, seed: int) -> str:
    """Deterministic filename hash from entity parts + seed."""
    h = hashlib.sha256("|".join(parts + [str(seed)]).encode()).hexdigest()[:10]
    return h


def _gray_zone_doc_content() -> str:
    """Fixed document containing the Paris person/city ambiguity."""
    return """# Paris — Person or Place?

Paris is a researcher at Meridian Labs who studies urban development.

Paris recently moved to Berlin for a sabbatical. Before that, Paris spent
two years working in Paris, the capital of France.

Note: "Paris" appears here referring to both a person and a city.
The person Paris and the city Paris are distinct entities that share a name.
"""


def make_corpus(n_docs: int, out_dir: Path | str, *, seed: int = 42) -> Path:
    """Generate a deterministic benchmark corpus.

    Args:
        n_docs: Number of documents to generate.
        out_dir: Directory to write corpus files into.
        seed: RNG seed for deterministic output.

    Returns:
        Path to the manifest file.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = out_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    rng_seed = seed
    state = rng_seed

    def next_int(mod: int) -> int:
        nonlocal state
        # Simple LCG for deterministic sequence
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state % mod

    # Build entity assignments — each doc gets person, org, location, event, fact, pref
    docs_content: list[tuple[str, str]] = []  # (filename, content)
    used_entities: set[str] = set()

    for i in range(n_docs):
        p = _PERSONS[next_int(len(_PERSONS))]
        o = _ORGS[next_int(len(_ORGS))]
        l = _LOCATIONS[next_int(len(_LOCATIONS))]
        e = _EVENTS[next_int(len(_EVENTS))]
        f = _FACTS[next_int(len(_FACTS))]
        pr = _PREFERENCES[next_int(len(_PREFERENCES))]
        # Cross-reference: pick a different person from the one assigned
        cross_idx = next_int(len(_PERSONS))
        while cross_idx == _PERSONS.index(p) and len(_PERSONS) > 1:
            cross_idx = next_int(len(_PERSONS))
        cross_p = _PERSONS[cross_idx]

        template = _DOC_TEMPLATES[i % len(_DOC_TEMPLATES)]
        sections_fn = _SECTION_FNS[template[1]]
        lines = sections_fn(p, o, l, e, f, pr, cross_p)
        content = "\n".join(lines) + "\n"

        used_entities.update([p[0], o[0], l[0], e[0]])

        filename = f"{_stable_hash([p[0], o[0], template[0]], seed=seed)}-{i:04d}.md"
        docs_content.append((filename, content))

    # Write corpus files
    checksums: list[str] = []
    for filename, content in docs_content:
        fpath = corpus_dir / filename
        fpath.write_text(content, encoding="utf-8")
        checksums.append(hashlib.sha256(content.encode()).hexdigest())

    # Write gray-zone document
    gray_content = _gray_zone_doc_content()
    gray_path = corpus_dir / "gray-zone-paris.md"
    gray_path.write_text(gray_content, encoding="utf-8")

    # Write near-duplicate pair — same person, different content
    dup_base = _PERSONS[0]  # Elena Vasquez
    dup_content_1 = f"""# Profile: {dup_base[0]}

{dup_base[0]} is a leading researcher at Meridian Labs.
She specializes in knowledge graph systems and semantic analysis.

## Fact

{_FACTS[0][0]}

## Preferences

{_PREFERENCES[0][0]}
"""
    dup_content_2 = f"""# Profile: {dup_base[0]}

{dup_base[0]} is a leading researcher at Meridian Labs.
She focuses on knowledge graph architectures and semantic parsing.

## Fact

{_FACTS[0][0]}

## Preferences

{_PREFERENCES[0][0]}
"""
    dup_path_1 = corpus_dir / "near-dup-elena-vasquez-a.md"
    dup_path_2 = corpus_dir / "near-dup-elena-vasquez-b.md"
    dup_path_1.write_text(dup_content_1, encoding="utf-8")
    dup_path_2.write_text(dup_content_2, encoding="utf-8")

    total_files = n_docs + 3  # n_docs + gray-zone + 2 near-dups

    # Write manifest
    manifest = {
        "doc_count": total_files,
        "generated_docs": n_docs,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checksums": checksums,
        "gray_zone_pairs": [("Paris", "person"), ("Paris", "location")],
        "near_duplicate_pairs": [
            {
                "files": ["near-dup-elena-vasquez-a.md", "near-dup-elena-vasquez-b.md"],
                "entity": "Elena Vasquez",
            }
        ],
        "used_entities": sorted(used_entities),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return manifest_path


def make_corpus_at_scale(scale: int, base_dir: Path | str, *, seed: int = 42) -> Path:
    """Generate a corpus at a named scale with isolated output dir.

    Args:
        scale: One of 10, 50, 250.
        base_dir: Base directory; creates ``scale-{scale}/`` underneath.
        seed: RNG seed.

    Returns:
        Path to the manifest file.
    """
    if scale not in (10, 50, 250):
        raise ValueError(f"scale must be 10, 50, or 250, got {scale}")
    base_dir = Path(base_dir)
    out_dir = base_dir / f"scale-{scale}"
    return make_corpus(scale, out_dir, seed=seed)
