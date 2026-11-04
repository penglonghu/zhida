"""分块调参：扫描 chunk_size / chunk_overlap 对检索指标的影响。

用法：
    .venv/bin/python scripts/sweep_chunking.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.doc_parser import split_text
from core.rag_retriever import retriever
from eval_retrieval import load_testset, evaluate


def ingest(chunk_size: int, chunk_overlap: int) -> int:
    corpus = json.loads(Path("docs/eval_corpus.json").read_text(encoding="utf-8"))
    retriever.clear()
    total = 0
    for doc in corpus:
        chunks = split_text(doc["content"], chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        total += retriever.add_chunks(chunks, doc_name=doc["name"])
    return total


def main():
    testset = load_testset(Path("scripts/testset.json"))
    print(f"{'chunk_size':>10} | {'overlap':>7} | {'chunks':>6} | hit@3   | MRR")
    print("-" * 54)
    for chunk_size in [200, 350, 500, 800]:
        for overlap in [0, 80]:
            total = ingest(chunk_size, overlap)
            r = evaluate(testset, 3, "hybrid")
            print(f"{chunk_size:>10} | {overlap:>7} | {total:>6} | {r['hit_rate']:.2%} | {r['mrr']:.3f}")


if __name__ == "__main__":
    main()
