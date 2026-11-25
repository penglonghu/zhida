"""文档列举/删除的离线单测：用可注入的 fake Qdrant client，不连接真实向量库。"""
from types import SimpleNamespace

from core.rag_retriever import Retriever


def _point(pid: str, tenant: str, kb: str, doc: str, idx: int = 0):
    return SimpleNamespace(id=pid, payload={
        "text": f"{doc}-{idx}",
        "doc_name": doc,
        "chunk_index": idx,
        "tenant_id": tenant,
        "kb_id": kb,
    })


class FakeQdrantClient:
    """只实现 Retriever 用到的 Qdrant 接口，按 payload 过滤模拟服务端行为。"""

    def __init__(self, points):
        self.points = {p.id: p for p in points}
        self.payload_schema = {}
        self.created_indexes = []

    def collection_exists(self, collection_name):
        return True

    def get_collection(self, collection_name):
        return SimpleNamespace(payload_schema=self.payload_schema)

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.payload_schema[field_name] = field_schema
        self.created_indexes.append(field_name)

    @staticmethod
    def _matches(payload, flt):
        if flt is None:
            return True
        for cond in flt.must:
            if payload.get(cond.key) != cond.match.value:
                return False
        return True

    def scroll(self, collection_name, scroll_filter=None, with_payload=None,
               with_vectors=False, limit=10, offset=None):
        matched = [p for p in self.points.values() if self._matches(p.payload, scroll_filter)]
        matched.sort(key=lambda p: str(p.id))
        start = int(offset or 0)
        page = matched[start:start + limit]
        next_offset = start + limit if start + limit < len(matched) else None
        return page, next_offset

    def count(self, collection_name, count_filter=None, exact=True):
        return SimpleNamespace(
            count=sum(1 for p in self.points.values() if self._matches(p.payload, count_filter))
        )

    def delete(self, collection_name, points_selector):
        flt = points_selector.filter
        doomed = [pid for pid, p in self.points.items() if self._matches(p.payload, flt)]
        for pid in doomed:
            del self.points[pid]


def _retriever(points):
    r = Retriever()
    r._client = FakeQdrantClient(points)
    return r


def test_ensure_collection_indexes_doc_name():
    r = _retriever([])
    r.ensure_collection()
    schema = r.client.get_collection(r.collection_name).payload_schema
    assert "doc_name" in schema
    assert {"tenant_id", "kb_id"} <= set(schema)


def test_list_documents_empty():
    assert _retriever([]).list_documents("t1", "kb1") == []


def test_list_documents_aggregates_and_sorts():
    r = _retriever([
        _point("p1", "t1", "kb1", "B.pdf"),
        _point("p2", "t1", "kb1", "A.pdf", 0),
        _point("p3", "t1", "kb1", "A.pdf", 1),
        _point("p4", "t1", "kb1", "A.pdf", 2),
    ])
    docs = r.list_documents("t1", "kb1")
    assert [(d.doc_name, d.chunk_count) for d in docs] == [("A.pdf", 3), ("B.pdf", 1)]


def test_list_documents_isolated_by_scope():
    r = _retriever([
        _point("p1", "t1", "kb1", "A.pdf"),
        _point("p2", "t2", "kb1", "B.pdf"),
        _point("p3", "t1", "kb2", "C.pdf"),
    ])
    docs = r.list_documents("t1", "kb1")
    assert [(d.doc_name, d.chunk_count) for d in docs] == [("A.pdf", 1)]


def test_list_documents_scrolls_all_pages():
    r = _retriever([_point(f"p{i}", "t1", "kb1", "big.pdf", i) for i in range(501)])
    docs = r.list_documents("t1", "kb1")
    assert [(d.doc_name, d.chunk_count) for d in docs] == [("big.pdf", 501)]


def test_delete_document_removes_all_chunks():
    r = _retriever([
        _point("p1", "t1", "kb1", "A.pdf", 0),
        _point("p2", "t1", "kb1", "A.pdf", 1),
        _point("p3", "t1", "kb1", "B.pdf"),
    ])
    assert r.delete_document("t1", "kb1", "A.pdf") == 2
    assert [(d.doc_name, d.chunk_count) for d in r.list_documents("t1", "kb1")] == [("B.pdf", 1)]


def test_delete_missing_document_returns_zero():
    r = _retriever([_point("p1", "t1", "kb1", "A.pdf")])
    assert r.delete_document("t1", "kb1", "nope.pdf") == 0
    assert len(r.client.points) == 1


def test_delete_does_not_cross_scope():
    r = _retriever([
        _point("p1", "t1", "kb1", "A.pdf"),
        _point("p2", "t2", "kb1", "A.pdf"),
        _point("p3", "t1", "kb2", "A.pdf"),
    ])
    assert r.delete_document("t1", "kb1", "A.pdf") == 1
    assert len(r.client.points) == 2
    assert [(d.doc_name, d.chunk_count) for d in r.list_documents("t2", "kb1")] == [("A.pdf", 1)]
    assert [(d.doc_name, d.chunk_count) for d in r.list_documents("t1", "kb2")] == [("A.pdf", 1)]


def test_delete_invalidates_cache():
    r = _retriever([_point("p1", "t1", "kb1", "A.pdf")])
    r._loaded = True
    r._points = {"p1": object()}
    r._scope_ids = {("t1", "kb1"): ["p1"]}
    r._scope_bm25 = {("t1", "kb1"): object()}
    assert r.delete_document("t1", "kb1", "A.pdf") == 1
    assert r._loaded is False
    assert r._points == {}
    assert r._scope_ids == {}
    assert r._scope_bm25 == {}
