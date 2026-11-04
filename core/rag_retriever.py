import logging
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from core.bm25 import BM25
from core.embedding import embedding_client
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class RetrievalHit:
    """单条检索命中：文本 + 分数 + 来源元数据"""
    text: str
    score: float
    doc_name: str = ""
    chunk_index: int = -1


class Retriever:
    def __init__(self):
        self._client = None
        self.collection_name = settings.qdrant_collection_name
        self.vector_dim = settings.vector_dim
        # 混合检索本地缓存：point_id -> 元数据 + BM25 索引（id 列表与索引同序）
        self._points: Dict[str, RetrievalHit] = {}
        self._bm25_ids: List[str] = []
        self._bm25: Optional[BM25] = None

    @property
    def client(self):
        if self._client is None:
            logger.info("连接 %s:%s", settings.qdrant_host, settings.qdrant_port)
            self._client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        return self._client

    def ensure_collection(self):
        exist = self.client.collection_exists(self.collection_name)
        if not exist:
            logger.info("创建集合 %s, dim=%s", self.collection_name, self.vector_dim)
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.vector_dim, distance=Distance.COSINE)
            )

    def _invalidate_cache(self):
        self._points = {}
        self._bm25_ids = []
        self._bm25 = None

    def _load_points(self):
        """拉取集合全部 point 到内存并构建 BM25 索引（混合检索用）。"""
        if self._bm25 is not None:
            return
        self.ensure_collection()
        points: Dict[str, RetrievalHit] = {}
        texts: List[str] = []
        ids: List[str] = []
        offset = None
        while True:
            res, offset = self.client.scroll(
                collection_name=self.collection_name,
                with_payload=True,
                with_vectors=False,
                limit=500,
                offset=offset,
            )
            for p in res:
                payload = p.payload or {}
                hit = RetrievalHit(
                    text=payload.get("text", ""),
                    score=0.0,
                    doc_name=payload.get("doc_name", ""),
                    chunk_index=payload.get("chunk_index", -1),
                )
                points[str(p.id)] = hit
                texts.append(hit.text)
                ids.append(str(p.id))
            if offset is None:
                break
        self._points = points
        self._bm25_ids = ids
        self._bm25 = BM25()
        self._bm25.fit(texts)

    def add_chunks(self, chunks: List[str], doc_name: str = "") -> int:
        if not chunks:
            return 0
        self.ensure_collection()
        vectors = embedding_client.encode(chunks)
        points = []
        for i, (text, vec) in enumerate(zip(chunks, vectors)):
            point_id = str(uuid.uuid4())
            payload = {"text": text, "doc_name": doc_name, "chunk_index": i}
            points.append(PointStruct(id=point_id, vector=vec, payload=payload))
        self.client.upsert(collection_name=self.collection_name, points=points)
        self._invalidate_cache()
        return len(points)

    def search_dense_with_score(self, query: str, limit: Optional[int] = None) -> List[RetrievalHit]:
        self.ensure_collection()
        query_vec = embedding_client.encode_single(query)
        top_k = limit or settings.retrieve_top_k
        res = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            with_payload=True,
            limit=top_k
        )
        hits = []
        for p in res.points:
            payload = p.payload or {}
            hits.append(RetrievalHit(
                text=payload.get("text", ""),
                score=p.score,
                doc_name=payload.get("doc_name", ""),
                chunk_index=payload.get("chunk_index", -1),
            ))
        return hits

    def search_hybrid_with_score(self, query: str, limit: Optional[int] = None) -> List[RetrievalHit]:
        self.ensure_collection()
        top_k = limit or settings.retrieve_top_k
        self._load_points()
        if not self._points:
            return []

        # 1) 稠密召回候选
        query_vec = embedding_client.encode_single(query)
        dense_res = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            with_payload=True,
            limit=min(settings.hybrid_dense_candidates, len(self._points)),
        )
        # 2) 关键词召回候选（BM25）
        kw_top = self._bm25.search(query, settings.hybrid_keyword_candidates)

        # 3) RRF 融合：同一文档在两条排序列表里的加权排名倒数之和
        rrf: Dict[str, float] = {}
        for rank, p in enumerate(dense_res.points, start=1):
            rrf[str(p.id)] = rrf.get(str(p.id), 0.0) + settings.rrf_dense_weight / (settings.rrf_k + rank)
        for rank, (idx, _score) in enumerate(kw_top, start=1):
            pid = self._bm25_ids[idx]
            rrf[pid] = rrf.get(pid, 0.0) + settings.rrf_keyword_weight / (settings.rrf_k + rank)

        ranked = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        hits = []
        for pid, score in ranked:
            hit = self._points[pid]
            hits.append(RetrievalHit(
                text=hit.text,
                score=round(score, 6),
                doc_name=hit.doc_name,
                chunk_index=hit.chunk_index,
            ))
        return hits

    def search(self, query: str, limit: Optional[int] = None) -> List[str]:
        if settings.retrieval_mode == "hybrid":
            hits = self.search_hybrid_with_score(query, limit)
        else:
            hits = self.search_dense_with_score(query, limit)
        return [h.text for h in hits]

    def clear(self):
        self.client.delete_collection(self.collection_name)
        self.ensure_collection()
        self._invalidate_cache()

    def list_all_chunks(self) -> List[str]:
        self.ensure_collection()
        scroll_res = self.client.scroll(collection_name=self.collection_name, with_payload=True, limit=1000)
        chunks = [p.payload["text"] for p in scroll_res[0]]
        return chunks


retriever = Retriever()
