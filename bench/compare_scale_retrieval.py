"""Scale test: indexed graph retrieval vs flat-folder scan."""
from __future__ import annotations
import tempfile, time
from pathlib import Path
import tiktoken
from kg.cli.init import init_project
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import FakeEmbedder
from kg.ontology import Node
from kg.search import hybrid_search
from kg.traverse import expand
from kg.pack import pack
from kg.config import Config

N = 10_000
ENC = tiktoken.get_encoding("cl100k_base")

def main():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d); docs=root/'docs'; docs.mkdir()
        for i in range(N):
            name = 'Meridian Labs' if i % 1000 == 0 else f'Other Team {i}'
            (docs/f'{i}.md').write_text(f'# {name}\n\nDocument {i}. {name} builds knowledge graph systems.\n')
        paths=init_project(root, user_id='bench', scope='flat-v-graph')
        cfg=Config.from_path(paths.config); a=SQLiteAdapter(paths.kg_db); e=FakeEmbedder()
        nodes=[Node(id=f'b:document:{i}',type='document',name=f'{i}.md',summary=('Meridian Labs builds knowledge graph systems.' if i%1000==0 else f'Other Team {i}')) for i in range(N)]
        a.upsert_nodes(nodes)
        q='Meridian Labs'; expected='Meridian Labs'
        t=time.perf_counter(); flat='\n'.join(p.read_text() for p in docs.iterdir() if q in p.read_text()); flat_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter(); hits=hybrid_search(a,e,q,k=10,config=cfg); sub=expand(a,[n for n,_ in hits],hops=1,cap=20); graph=pack(sub,dict(hits),rrf_scores=dict(hits),budget_tokens=128); graph_ms=(time.perf_counter()-t)*1000
        print('| Scale | Method | Tokens | Wall ms | Correct |')
        print('|---|---|---:|---:|---|')
        print(f'| {N} docs | Flat folder | {len(ENC.encode(flat))} | {flat_ms:.2f} | {expected in flat} |')
        print(f'| {N} docs | Graph pack | {len(ENC.encode(graph))} | {graph_ms:.2f} | {expected in graph} |')
        a.conn.close()
if __name__=='__main__': main()
