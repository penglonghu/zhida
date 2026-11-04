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


def tokenize(text: str) -> List[str]:
    tokens = [m.lower() for m in _ALNUM.findall(text)]
    cjk = _CJK.findall(text)
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
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

    def search(self, query: str, top_n: int) -> List[Tuple[int, float]]:
        """返回 [(doc_index, score)] 按分数降序，最多 top_n 条。"""
        q_terms = tokenize(query)
        if not q_terms or self.N == 0:
            return []
        scored: List[Tuple[int, float]] = []
        for i, tf in enumerate(self.docs):
            dl = self.doc_len[i]
            score = 0.0
            for t in set(q_terms):
                f = tf.get(t, 0)
                if f == 0:
                    continue
                idf = self._idf(t)
                score += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if score > 0:
                scored.append((i, score))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:top_n]
