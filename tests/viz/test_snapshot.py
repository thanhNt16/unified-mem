import json

from kg.storage.sqlite import SQLiteAdapter
from kg.viz.api import build_layout_payload, build_snapshot_payload


def test_static_snapshot_equals_live_payload(seeded_adapter: SQLiteAdapter) -> None:
    live = build_layout_payload(seeded_adapter)
    static = build_snapshot_payload(seeded_adapter)
    assert static == live
    assert json.dumps(static, sort_keys=True, separators=(",", ":"), allow_nan=False)
