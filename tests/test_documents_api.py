"""文档管理 API 的离线单测：TestClient + stub retriever，不连接 Qdrant/LLM。"""
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from core.rag_retriever import DocumentSummary
from schemas import DocumentInfo


class StubRetriever:
    def __init__(self):
        self.docs = {"a.pdf": 3, "中文.pdf": 2}
        self.list_scope = None
        self.delete_calls = []

    def list_documents(self, tenant_id, kb_id):
        self.list_scope = (tenant_id, kb_id)
        return [DocumentSummary(doc_name=n, chunk_count=c) for n, c in sorted(self.docs.items())]

    def delete_document(self, tenant_id, kb_id, doc_name):
        self.delete_calls.append((tenant_id, kb_id, doc_name))
        return self.docs.pop(doc_name, 0)


@pytest.fixture
def api(monkeypatch):
    stub = StubRetriever()
    monkeypatch.setattr(main, "retriever", stub)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    with TestClient(main.app) as client:
        yield client, stub


def test_document_info_serialization():
    assert DocumentInfo(doc_name="a.pdf", chunk_count=2).model_dump() == {
        "doc_name": "a.pdf",
        "chunk_count": 2,
    }


def test_openapi_contains_document_schemas(api):
    schema_names = api[0].get("/openapi.json").json()["components"]["schemas"]
    assert {"DocumentInfo", "DocumentListResponse", "DocumentDeleteResponse"} <= set(schema_names)


def test_list_documents_returns_aggregated_docs(api):
    client, stub = api
    res = client.get("/documents", params={"kb_id": "kbX"}, headers={"X-Tenant-Id": "t1"})
    assert res.status_code == 200
    assert res.json() == {"documents": [
        {"doc_name": "a.pdf", "chunk_count": 3},
        {"doc_name": "中文.pdf", "chunk_count": 2},
    ]}
    assert stub.list_scope == ("t1", "kbX")


def test_delete_document_success(api):
    client, stub = api
    res = client.delete("/documents/a.pdf", params={"kb_id": "kb1"}, headers={"X-Tenant-Id": "t1"})
    assert res.status_code == 200
    assert res.json()["removed_chunks"] == 3
    assert "a.pdf" not in stub.docs
    assert stub.delete_calls == [("t1", "kb1", "a.pdf")]


def test_delete_document_not_found(api):
    client, _ = api
    res = client.delete("/documents/missing.pdf")
    assert res.status_code == 404


@pytest.mark.parametrize("bad_name", ["%20", "a..b", "a%5Cb"])
def test_delete_document_rejects_invalid_names(api, bad_name):
    client, stub = api
    res = client.delete(f"/documents/{bad_name}")
    assert res.status_code == 400
    assert stub.delete_calls == []


def test_delete_document_accepts_chinese_name(api):
    client, stub = api
    res = client.delete(f"/documents/{quote('中文.pdf')}", params={"kb_id": "kb2"})
    assert res.status_code == 200
    assert res.json()["removed_chunks"] == 2
    assert "中文.pdf" not in stub.docs
