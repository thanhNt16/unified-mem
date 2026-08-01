"""Prove graph retrieval beats flat-folder context on identical questions."""
from __future__ import annotations
import json, tempfile, time
from pathlib import Path
import tiktoken
from bench.corpus import make_corpus
from bench.runners import build_indexed_adapter, _documents
from kg.router import classify
from kg.search import graph_aware_hybrid_search
from kg.traverse import expand
from kg.pack import pack, diversify_by_source, adaptive_budget

ENC = tiktoken.get_encoding("cl100k_base")
QUESTIONS = [
    ("Meridian Labs", ["Meridian Labs"]),
    ("Paris", ["Paris"]),
    ("Cascade AI", ["Cascade AI"]),
    ("DataSummit 2024", ["DataSummit 2024"]),
    ("knowledge graph", ["knowledge graph"]),
]

def tokens(text: str) -> int:
    return len(ENC.encode(text))

def main() -> None:
    rows=[]
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); corpus_root=td/"corpus"; make_corpus(50, corpus_root, seed=42)
        corpus=corpus_root/"corpus"; adapter, emb, cfg = build_indexed_adapter(corpus, td/"kg", scale=50)
        try:
            files={p.name:p.read_text(encoding="utf-8") for p in _documents(corpus)}
            for q, expected in QUESTIONS:
                # Flat folder: same lexical first-pass docs, then raw full files (what user pastes)
                t0=time.perf_counter()
                flat_docs=[v for v in files.values() if any(term.lower() in v.lower() for term in expected)]
                flat="\n\n--- SOURCE ---\n\n".join(flat_docs)
                flat_ms=(time.perf_counter()-t0)*1000
                # Graph: same question -> policy -> RRF -> bounded neighborhood -> pack
                t0=time.perf_counter(); policy=classify(q, cfg)
                ranked=graph_aware_hybrid_search(adapter,emb,q,policy,config=cfg)
                ranked=diversify_by_source(ranked,adapter,max_per_source=cfg.query.diversity_cap)
                ids=[nid for nid,_ in ranked]; sub=expand(adapter,ids,hops=policy.hops,cap=cfg.query.subgraph_cap) if ids else None
                graph=pack(sub,{nid:s for nid,s in ranked},rrf_scores=dict(ranked),budget_tokens=256) if sub else ""
                graph_ms=(time.perf_counter()-t0)*1000
                # Correctness: expected literal appears in returned context
                flat_ok=all(x.lower() in flat.lower() for x in expected)
                graph_ok=all(x.lower() in graph.lower() for x in expected)
                rows.append({"question":q,"flat_tokens":tokens(flat),"graph_tokens":tokens(graph),"flat_ms":round(flat_ms,3),"graph_ms":round(graph_ms,3),"flat_ok":flat_ok,"graph_ok":graph_ok,"graph_hits":len(ranked)})
        finally: adapter.conn.close()
    print("| Question | Flat tokens | Graph tokens | Token saved | Flat ms | Graph ms | Flat correct | Graph correct |")
    print("|---|---:|---:|---:|---:|---:|---|---|")
    for r in rows:
        saved=1-r["graph_tokens"]/max(r["flat_tokens"],1)
        print(f"| {r['question']} | {r['flat_tokens']} | {r['graph_tokens']} | {saved:.1%} | {r['flat_ms']} | {r['graph_ms']} | {r['flat_ok']} | {r['graph_ok']} |")
    print(json.dumps(rows,indent=2))
if __name__=="__main__": main()
