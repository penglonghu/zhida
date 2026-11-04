"""检索召回评估：给定测试集，计算 hit@k 与 MRR。

测试集（scripts/testset.json）每条：
    {"question": "问题", "gold": "正确chunk必须包含的子串"}

hit@k：gold 出现在 top-k 命中里的查询占比（成功@k）。
MRR  ：gold 首次出现位置的倒数平均，衡量排序质量。

用法：
    .venv/bin/python scripts/eval_retrieval.py [--top-k 3] [--mode dense|hybrid] [--testset scripts/testset.json]
"""
import argparse
import json
import sys
from pathlib import Path

# 让脚本能从仓库根目录 import core
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.rag_retriever import retriever


def load_testset(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(testset: list[dict], top_k: int, mode: str = "dense") -> dict:
    hit_count = 0
    rr_sum = 0.0
    rows = []
    for item in testset:
        question = item["question"]
        gold = item["gold"]
        if mode == "hybrid":
            results = retriever.search_hybrid_with_score(question, limit=top_k)
        else:
            results = retriever.search_dense_with_score(question, limit=top_k)

        rank = None
        for i, h in enumerate(results, start=1):
            if gold in h.text:
                rank = i
                break

        if rank is not None:
            hit_count += 1
            rr_sum += 1.0 / rank

        rows.append({
            "question": question,
            "gold": gold,
            "rank": rank,
            "top": [(round(h.score, 4), h.doc_name, h.chunk_index) for h in results],
        })

    n = len(testset)
    return {
        "total": n,
        "hit_count": hit_count,
        "hit_rate": hit_count / n if n else 0.0,
        "mrr": rr_sum / n if n else 0.0,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--testset", type=str, default="scripts/testset.json")
    parser.add_argument("--mode", type=str, default="dense", choices=["dense", "hybrid"])
    args = parser.parse_args()

    testset = load_testset(Path(args.testset))
    result = evaluate(testset, args.top_k, args.mode)

    print(f"测试集条数 : {result['total']}")
    print(f"top_k      : {args.top_k}")
    print(f"mode       : {args.mode}")
    print(f"hit@{args.top_k}   : {result['hit_rate']:.2%}  ({result['hit_count']}/{result['total']})")
    print(f"MRR        : {result['mrr']:.3f}")
    print("-" * 70)
    for row in result["rows"]:
        status = f"命中@rank{row['rank']}" if row["rank"] else "未命中"
        top_str = ", ".join(f"{s}({d}#{i})" for s, d, i in row["top"])
        print(f"Q: {row['question']}")
        print(f"   {status}  |  gold={row['gold']!r}")
        print(f"   top: {top_str}")
    return result


if __name__ == "__main__":
    main()
