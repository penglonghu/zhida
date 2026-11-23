"""多轮追问实验：同一批追问，比较「原样检索」与「改写后检索」的排名差异。

背景：多轮对话里用户常问「那它呢」「第二个呢」这类纯指代追问，检索器拿到这种
查询只能随机返回。真实系统里靠一次 LLM 调用把追问改写成独立句子（见
core/llm.py:rewrite_query），但这个调用值不值，需要用数据说话。

本脚本不依赖 Qdrant / Redis / LLM API，纯本地可跑：
    python3 scripts/eval_multiturn.py

口径与线上检索一致：split_text(500/80) + 本地 BGE 稠密 + BM25 + 加权 RRF
（rrf_k=60、dense 1.0、keyword 3.0，两路各取 20 个候选）。改写后的句子在这里
是预先写好的（真实环境由 LLM 生成），这样比较的变量只剩「查询文本本身」。

依赖：sentence-transformers、models/bge-small-zh（随仓库提供）。
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.bm25 import BM25                      # noqa: E402
from core.doc_parser import split_text          # noqa: E402

CORPUS = json.loads((ROOT / "docs/eval_corpus.json").read_text(encoding="utf-8"))
MODEL_DIR = ROOT / "models/bge-small-zh"

K_RRF, W_DENSE, W_KEYWORD, CANDIDATES, TOP_N = 60, 1.0, 3.0, 20, 3

# (上一问, 原样追问, 改写后的独立查询, 答案必须包含的文字)
CASES_WITH_CONTENT_WORDS = [
    ("知答 3.0 什么时候发布？", "那它的首发定价呢？", "知答 3.0 的首发定价是多少", "每席位每年 9800 元"),
    ("第一季度营业收入多少？", "那第二季度呢？", "第二季度营业收入是多少", "1.28 亿元"),
    ("CVE-2026-33821 是什么风险等级？", "那它几个工作日内要修完？", "CVE-2026-33821 的补丁升级要求几个工作日", "3 个工作日"),
    ("服务器 A 的内存多大？", "那 C 呢？", "服务器 C 的配置是什么", "GPU 8 卡 A100"),
    ("知答私有化部署用什么向量库？", "那公有云呢？", "知答公有云部署需要安装什么", "对象存储"),
    ("BGE-small-zh 输出多少维？", "那 large 呢？", "BGE-large-zh 输出多少维", "1024 维"),
]

CASES_PURE_ANAPHORA = [
    ("知答 3.0 什么时候发布？", "那它呢？", "知答 3.0 的首发定价是多少", "每席位每年 9800 元"),
    ("第一季度营业收入多少？", "第二个呢？", "第二季度营业收入是多少", "1.28 亿元"),
    ("CVE-2026-33821 是什么风险等级？", "那个要几天修完？", "CVE-2026-33821 的补丁升级要求几个工作日", "3 个工作日"),
    ("服务器 A 的内存多大？", "它呢？", "服务器 C 的配置是什么", "GPU 8 卡 A100"),
    ("知答私有化部署用什么向量库？", "那个呢？", "知答公有云部署需要安装什么", "对象存储"),
    ("BGE-small-zh 输出多少维？", "它的呢？", "BGE-large-zh 输出多少维", "1024 维"),
]


def build_index(model):
    chunks, doc_names = [], []
    for doc in CORPUS:
        for chunk in split_text(doc["content"]):
            chunks.append(chunk)
            doc_names.append(doc["name"])
    bm25 = BM25()
    bm25.fit(chunks)
    emb = np.asarray(
        model.encode(chunks, normalize_embeddings=True, show_progress_bar=False),
        dtype="float32",
    )
    return chunks, doc_names, bm25, emb


def dense_hits(model, emb, query: str, k: int = CANDIDATES):
    qv = np.asarray(model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0], dtype="float32")
    scores = emb @ qv
    return [(int(i), float(scores[i])) for i in np.argsort(-scores)[:k]]


def hybrid_hits(model, emb, bm25, query: str, top_n: int = TOP_N):
    """加权 RRF：两路排名倒数和，关键词权重更高（中文垂直场景实测 1.0 偏低）。"""
    fused = {}
    for rank, (idx, _) in enumerate(dense_hits(model, emb, query), start=1):
        fused[idx] = fused.get(idx, 0.0) + W_DENSE / (K_RRF + rank)
    for rank, (idx, _) in enumerate(bm25.search(query, top_n=CANDIDATES), start=1):
        fused[idx] = fused.get(idx, 0.0) + W_KEYWORD / (K_RRF + rank)
    return sorted(fused.items(), key=lambda kv: -kv[1])[:top_n]


def rank_of(gold: str, hits, chunks):
    for rank, (idx, _) in enumerate(hits, start=1):
        if gold in chunks[idx]:
            return rank
    return None


def run_group(model, emb, bm25, chunks, title, cases):
    print(f"\n=== {title} ===")
    raw_hits = rewritten_hits = 0
    for prev_q, raw, rewritten, gold in cases:
        r_raw = rank_of(gold, hybrid_hits(model, emb, bm25, raw), chunks)
        r_rw = rank_of(gold, hybrid_hits(model, emb, bm25, rewritten), chunks)
        raw_hits += bool(r_raw and r_raw <= TOP_N)
        rewritten_hits += bool(r_rw and r_rw <= TOP_N)
        fmt = lambda r: f"@{r}" if r else "未命中"   # noqa: E731
        print(f"  {prev_q}  +  {raw:<14} 原样 {fmt(r_raw):<6} 改写 {fmt(r_rw)}")
    total = len(cases)
    print(f"  → hit@{TOP_N}：原样 {raw_hits}/{total}，改写 {rewritten_hits}/{total}")
    return raw_hits, rewritten_hits, total


def main():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(str(MODEL_DIR))
    chunks, _, bm25, emb = build_index(model)

    a = run_group(model, emb, bm25, chunks, "A 组 · 带实词的追问", CASES_WITH_CONTENT_WORDS)
    b = run_group(model, emb, bm25, chunks, "B 组 · 纯指代追问", CASES_PURE_ANAPHORA)

    raw_total = a[0] + b[0]
    rw_total = a[1] + b[1]
    total = a[2] + b[2]
    print(f"\n合计：不改写 {raw_total}/{total}，改写 {rw_total}/{total}")
    print("解读见《RAG 多轮问答：12 个追问，4 个检索直接崩》")


if __name__ == "__main__":
    main()
