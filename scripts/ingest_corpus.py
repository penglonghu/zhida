"""把 docs/eval_corpus.json 里的合成语料切分并入库。

用法：
    .venv/bin/python scripts/ingest_corpus.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.doc_parser import split_text
from core.rag_retriever import retriever


def main():
    corpus_path = Path("docs/eval_corpus.json")
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))

    retriever.clear()
    total = 0
    for doc in corpus:
        chunks = split_text(doc["content"])
        cnt = retriever.add_chunks(chunks, doc_name=doc["name"])
        total += cnt
        print(f"{doc['name']}: {cnt} chunks")
    print(f"总计 {total} 个 chunk 入库")


if __name__ == "__main__":
    main()
