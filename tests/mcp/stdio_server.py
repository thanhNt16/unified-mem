"""Subprocess fixture: launches kg MCP stdio with deterministic embeddings."""
from __future__ import annotations

import argparse
from pathlib import Path

from kg.embed import FakeEmbedder
from kg.mcp.server import _init_options, build_server
from mcp.server.stdio import stdio_server


async def main(root: Path, allow_writes: bool) -> None:
    server = build_server(root, allow_writes=allow_writes, embedder=FakeEmbedder())
    async with stdio_server() as (read, write):
        await server.run(read, write, _init_options(server, allow_writes))


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.root, args.allow_writes))
