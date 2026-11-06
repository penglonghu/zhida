"""BM25 关键词检索（零第三方分词依赖）。

分词策略：
- 英文/数字/编号（IP、CVE 编号、版本号、数字）作为整体 token，捕捉精确匹配；
- 中文按字符 bigram 切分，捕捉局部字面匹配。

BM25 是关键词检索的经典排序公式，衡量「查询词在文档中出现的权重」：
    score(q, d) = Σ IDF(t) * tf * (k1+1) / (tf + k1 * (1 - b + b * dl/avgdl))
"""
import math
import re
from collections import Counter
from typing import List, Tuple

_ALNUM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*")
_CJK = re.compile(r"[一-鿿]")

# 查询侧停用字：由这些字构成的 bigram 多是「的甲」「是谁」这类语法噪声，
# 在长文合同/制度里会压过真正的答案块。
_STOP_CHARS = set("的了是在吗呢吧啊谁哪什么几多少这那请问")


def tokenize(text: str) -> List[str]:
    tokens = [m.lower() for m in _ALNUM.findall(text)]
    cjk = _CJK.findall(text)
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75,
                 max_df_ratio: float = 0.3, min_df_cutoff: int = 5):
        self.k1 = k1
        self.b = b
        # 查询词在超过 30% 的块里出现即视为泛词（如「甲方」「合同」），
        # 只靠 tf 刷分而不具区分度，打分时忽略。小库用绝对阈值兜底。
        self.max_df_ratio = max_df_ratio
        self.min_df_cutoff = min_df_cutoff
        self.docs: List[Counter] = []
        self.doc_len: List[int] = []
        self.doc_freq: Counter = Counter()
        self.avgdl: float = 0.0
        self.N: int = 0

    def fit(self, corpus: List[str]) -> None:
        self.docs = []
        self.doc_len = []
        self.doc_freq = Counter()
        self.N = len(corpus)
        for text in corpus:
            tf = Counter(tokenize(text))
            self.docs.append(tf)
            self.doc_len.append(sum(tf.values()))
            for t in tf:
                self.doc_freq[t] += 1
        self.avgdl = sum(self.doc_len) / self.N if self.N else 0.0

    def _idf(self, term: str) -> float:
        n = self.doc_freq.get(term, 0)
        return math.log((self.N - n + 0.5) / (n + 0.5) + 1)

    def _query_terms(self, query: str) -> List[str]:
        df_cutoff = max(self.min_df_cutoff, self.max_df_ratio * self.N)
        return [
            t for t in set(tokenize(query))
            if not (len(t) == 2 and (t[0] in _STOP_CHARS or t[1] in _STOP_CHARS))
            and self.doc_freq.get(t, 0) <= df_cutoff
        ]

    def search(self, query: str, top_n: int) -> List[Tuple[int, float]]:
        """返回 [(doc_index, score)] 按分数降序，最多 top_n 条。"""
        q_terms = self._query_terms(query)
        if not q_terms or self.N == 0:
            return []
        scored: List[Tuple[int, float]] = []
        for i, tf in enumerate(self.docs):
            dl = self.doc_len[i]
            score = 0.0
            for t in q_terms:
                f = tf.get(t, 0)
                if f == 0:
                    continue
                idf = self._idf(t)
                score += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if score > 0:
                scored.append((i, score))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:top_n]
