import logging
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, FilterSelector, MatchValue, PayloadSchemaType,
    PointStruct, VectorParams,
)

from core.bm25 import BM25
from core.embedding import embedding_client
from config import settings

logger = logging.getLogger(__name__)

Scope = Tuple[str, str]   # (tenant_id, kb_id)


@dataclass
class RetrievalHit:
    """单条检索命中：文本 + 分数 + 来源元数据"""
    text: str
    score: float
    doc_name: str = ""
    chunk_index: int = -1
    tenant_id: str = ""
    kb_id: str = ""


class Retriever:
    def __init__(self):
        self._client = None
        self.collection_name = settings.qdrant_collection_name
        self.vector_dim = settings.vector_dim
        # 全量点缓存 + 按租户/知识库划分的 id 列表与 BM25 索引
        self._points: Dict[str, RetrievalHit] = {}
        self._scope_ids: Dict[Scope, List[str]] = {}
        self._scope_bm25: Dict[Scope, BM25] = {}
        self._loaded = False
        self._indexes_ready = False

    @property
    def client(self):
        if self._client is None:
            logger.info("连接 %s:%s", settings.qdrant_host, settings.qdrant_port)
            self._client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        return self._client

    def _scope(self, tenant_id: Optional[str], kb_id: Optional[str]) -> Scope:
        return (
            settings.default_tenant_id if tenant_id is None else tenant_id,
            settings.default_kb_id if kb_id is None else kb_id,
        )

    @staticmethod
    def _scope_filter(tenant_id: str, kb_id: str) -> Filter:
        return Filter(must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            FieldCondition(key="kb_id", match=MatchValue(value=kb_id)),
        ])

    def ensure_collection(self):
        if self._indexes_ready:
            return
        if not self.client.collection_exists(self.collection_name):
            logger.info("创建集合 %s, dim=%s", self.collection_name, self.vector_dim)
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.vector_dim, distance=Distance.COSINE),
            )
        # 租户/知识库过滤字段建索引，否则大库过滤会退化成全表扫
        existing = (self.client.get_collection(self.collection_name).payload_schema or {})
        for field in ("tenant_id", "kb_id"):
            if field not in existing:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                logger.info("创建 payload 索引 %s", field)
        self._indexes_ready = True

    def _invalidate_cache(self):
        self._points = {}
        self._scope_ids = {}
        self._scope_bm25 = {}
        self._loaded = False

    def _load_points(self):
        """拉取集合全部 point 到内存并按 scope 分组（BM25 按需构建）。"""
        if self._loaded:
            return
        self.ensure_collection()
        points: Dict[str, RetrievalHit] = {}
        scope_ids: Dict[Scope, List[str]] = {}
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
                    tenant_id=payload.get("tenant_id", settings.default_tenant_id),
                    kb_id=payload.get("kb_id", settings.default_kb_id),
                )
                pid = str(p.id)
                points[pid] = hit
                scope_ids.setdefault((hit.tenant_id, hit.kb_id), []).append(pid)
            if offset is None:
                break
        self._points = points
        self._scope_ids = scope_ids
        self._loaded = True

    def _scope_index(self, tenant_id: str, kb_id: str) -> Tuple[List[str], Optional[BM25]]:
        """返回某个 scope 的 id 列表与 BM25 索引；索引首次使用时构建。"""
        self._load_points()
        key = (tenant_id, kb_id)
        ids = self._scope_ids.get(key, [])
        if not ids:
            return [], None
        if key not in self._scope_bm25:
            bm = BM25()
            bm.fit([self._points[i].text for i in ids])
            self._scope_bm25[key] = bm
        return ids, self._scope_bm25[key]

    def add_chunks(self, chunks: List[str], doc_name: str = "",
                   tenant_id: Optional[str] = None, kb_id: Optional[str] = None) -> int:
        if not chunks:
            return 0
        self.ensure_collection()
        tenant_id, kb_id = self._scope(tenant_id, kb_id)
        vectors = embedding_client.encode(chunks)
        points = []
        for i, (text, vec) in enumerate(zip(chunks, vectors)):
            payload = {
                "text": text,
                "doc_name": doc_name,
                "chunk_index": i,
                "tenant_id": tenant_id,
                "kb_id": kb_id,
            }
            points.append(PointStruct(id=str(uuid.uuid4()), vector=vec, payload=payload))
        self.client.upsert(collection_name=self.collection_name, points=points)
        self._invalidate_cache()
        return len(points)

    def search_dense_with_score(self, query: str, limit: Optional[int] = None,
                                tenant_id: Optional[str] = None,
                                kb_id: Optional[str] = None) -> List[RetrievalHit]:
        self.ensure_collection()
        tenant_id, kb_id = self._scope(tenant_id, kb_id)
        query_vec = embedding_client.encode_single(query)
        top_k = limit or settings.retrieve_top_k
        res = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            query_filter=self._scope_filter(tenant_id, kb_id),
            with_payload=True,
            limit=top_k,
        )
        return [self._to_hit(p) for p in res.points]

    def search_hybrid_with_score(self, query: str, limit: Optional[int] = None,
                                 tenant_id: Optional[str] = None,
                                 kb_id: Optional[str] = None) -> List[RetrievalHit]:
        self.ensure_collection()
        tenant_id, kb_id = self._scope(tenant_id, kb_id)
        top_k = limit or settings.retrieve_top_k
        ids, bm = self._scope_index(tenant_id, kb_id)
        if not ids or bm is None:
            return []

        # 1) 稠密召回候选（列举型会请求更深的 top_k，候选随之放大）
        query_vec = embedding_client.encode_single(query)
        dense_limit = min(max(settings.hybrid_dense_candidates, top_k), len(ids))
        dense_res = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            query_filter=self._scope_filter(tenant_id, kb_id),
            with_payload=True,
            limit=dense_limit,
        )
        # 2) 关键词召回候选（BM25，只在当前 scope 内打分）
        kw_top = bm.search(query, max(settings.hybrid_keyword_candidates, top_k))

        # 3) RRF 融合：同一文档在两条排序列表里的加权排名倒数之和
        rrf: Dict[str, float] = {}
        for rank, p in enumerate(dense_res.points, start=1):
            rrf[str(p.id)] = rrf.get(str(p.id), 0.0) + settings.rrf_dense_weight / (settings.rrf_k + rank)
        for rank, (idx, _score) in enumerate(kw_top, start=1):
            pid = ids[idx]
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
                tenant_id=hit.tenant_id,
                kb_id=hit.kb_id,
            ))
        return hits

    def _to_hit(self, p) -> RetrievalHit:
        payload = p.payload or {}
        return RetrievalHit(
            text=payload.get("text", ""),
            score=p.score,
            doc_name=payload.get("doc_name", ""),
            chunk_index=payload.get("chunk_index", -1),
            tenant_id=payload.get("tenant_id", settings.default_tenant_id),
            kb_id=payload.get("kb_id", settings.default_kb_id),
        )

    def search_with_meta(self, query: str, limit: Optional[int] = None,
                         tenant_id: Optional[str] = None,
                         kb_id: Optional[str] = None) -> List[RetrievalHit]:
        """按检索模式返回带来源元数据（doc_name/chunk_index）的命中。"""
        if settings.retrieval_mode == "hybrid":
            return self.search_hybrid_with_score(query, limit, tenant_id, kb_id)
        return self.search_dense_with_score(query, limit, tenant_id, kb_id)

    def search(self, query: str, limit: Optional[int] = None,
               tenant_id: Optional[str] = None, kb_id: Optional[str] = None) -> List[str]:
        return [h.text for h in self.search_with_meta(query, limit, tenant_id, kb_id)]

    def clear(self, tenant_id: Optional[str] = None, kb_id: Optional[str] = None):
        """清空。传 scope 只删该租户/知识库的块；都不传则删整个集合（脚本/管理员用）。"""
        if tenant_id is None and kb_id is None:
            if self.client.collection_exists(self.collection_name):
                self.client.delete_collection(self.collection_name)
            self._indexes_ready = False
            self.ensure_collection()
        else:
            tenant_id, kb_id = self._scope(tenant_id, kb_id)
            self.ensure_collection()
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(filter=self._scope_filter(tenant_id, kb_id)),
            )
        self._invalidate_cache()

    def list_all_chunks(self, tenant_id: Optional[str] = None,
                        kb_id: Optional[str] = None) -> List[str]:
        self.ensure_collection()
        query_filter = None
        if tenant_id is not None or kb_id is not None:
            t, k = self._scope(tenant_id, kb_id)
            query_filter = self._scope_filter(t, k)
        scroll_res = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=query_filter,
            with_payload=True,
            limit=1000,
        )
        return [p.payload["text"] for p in scroll_res[0]]

    def migrate_scope_fields(self) -> int:
        """把旧数据（无 tenant_id/kb_id）回填为默认 scope，返回更新条数。"""
        self.ensure_collection()
        updated = 0
        offset = None
        while True:
            res, offset = self.client.scroll(
                collection_name=self.collection_name,
                with_payload=True,
                with_vectors=False,
                limit=500,
                offset=offset,
            )
            ids = [
                p.id for p in res
                if "tenant_id" not in (p.payload or {}) or "kb_id" not in (p.payload or {})
            ]
            if ids:
                self.client.set_payload(
                    collection_name=self.collection_name,
                    payload={
                        "tenant_id": settings.default_tenant_id,
                        "kb_id": settings.default_kb_id,
                    },
                    points=ids,
                )
                updated += len(ids)
            if offset is None:
                break
        self._invalidate_cache()
        return updated


retriever = Retriever()
