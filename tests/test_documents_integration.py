"""真实 Qdrant 集成测试：验证文档列举/删除的租户与知识库隔离。

Qdrant 不可达时自动跳过；使用随机 collection，测试结束即删除，不碰业务库。
"""
import socket
import uuid

import pytest
from qdrant_client.models import PointStruct

from config import settings
from core.rag_retriever import Retriever


def _qdrant_up() -> bool:
    try:
        with socket.create_connection((settings.qdrant_host, settings.qdrant_port), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _qdrant_up(), reason="Qdrant not reachable")


@pytest.fixture
def retriever():
    r = Retriever()
    r.collection_name = f"zhida_it_{uuid.uuid4().hex[:10]}"
    yield r
    if r.client.collection_exists(r.collection_name):
        r.client.delete_collection(r.collection_name)


def _seed(r, tenant, kb, doc, count):
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.0] * settings.vector_dim,
            payload={
                "text": f"{doc}-{i}",
                "doc_name": doc,
                "chunk_index": i,
                "tenant_id": tenant,
                "kb_id": kb,
            },
        )
        for i in range(count)
    ]
    r.client.upsert(collection_name=r.collection_name, points=points)


def test_list_and_delete_isolated_in_real_qdrant(retriever):
    retriever.ensure_collection()
    schema = retriever.client.get_collection(retriever.collection_name).payload_schema
    assert "doc_name" in schema

    _seed(retriever, "t1", "kb1", "A.pdf", 2)
    _seed(retriever, "t1", "kb1", "B.pdf", 1)
    _seed(retriever, "t2", "kb1", "A.pdf", 1)
    _seed(retriever, "t1", "kb2", "A.pdf", 1)

    docs = [(d.doc_name, d.chunk_count) for d in retriever.list_documents("t1", "kb1")]
    assert docs == [("A.pdf", 2), ("B.pdf", 1)]

    assert retriever.delete_document("t1", "kb1", "A.pdf") == 2
    assert [(d.doc_name, d.chunk_count) for d in retriever.list_documents("t1", "kb1")] == [("B.pdf", 1)]
    assert [(d.doc_name, d.chunk_count) for d in retriever.list_documents("t2", "kb1")] == [("A.pdf", 1)]
    assert [(d.doc_name, d.chunk_count) for d in retriever.list_documents("t1", "kb2")] == [("A.pdf", 1)]
    assert retriever.delete_document("t1", "kb1", "A.pdf") == 0
