import logging
import uuid
from typing import List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from core.embedding import embedding_client
from config import settings

logger = logging.getLogger(__name__)


class Retriever:
    def __init__(self):
        self._client = None
        self.collection_name = settings.qdrant_collection_name
        self.vector_dim = settings.vector_dim

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

    def add_chunks(self, chunks: List[str]) -> int:
        if not chunks:
            return 0
        self.ensure_collection()
        vectors = embedding_client.encode(chunks)
        points = []
        for text, vec in zip(chunks, vectors):
            point_id = str(uuid.uuid4())
            points.append(PointStruct(id=point_id, vector=vec, payload={"text": text}))
        self.client.upsert(collection_name=self.collection_name, points=points)
        return len(points)

    def search(self, query: str) -> List[str]:
        self.ensure_collection()
        query_vec = embedding_client.encode_single(query)
        # 新版标准：query_points
        res = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vec,
            with_payload=True,
            limit=settings.retrieve_top_k
        )
        return [hit.payload["text"] for hit in res.points]

    def clear(self):
        self.client.delete_collection(self.collection_name)
        self.ensure_collection()

    def list_all_chunks(self) -> List[str]:
        self.ensure_collection()
        scroll_res = self.client.scroll(collection_name=self.collection_name, with_payload=True, limit=1000)
        chunks = [p.payload["text"] for p in scroll_res[0]]
        return chunks


retriever = Retriever()