import os
import uuid
from typing import List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from dotenv import load_dotenv
from core.embedding import embedding_client

load_dotenv()

QDRANT_HOST = os.getenv("QDRANT_HOST")
QDRANT_PORT = int(os.getenv("QDRANT_PORT"))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "fde_rag_demo")
VECTOR_DIM = int(os.getenv("VECTOR_DIM", 512))
TOP_K = int(os.getenv("RETRIEVE_TOP_K", 3))

class Retriever:
    def __init__(self):
        self._client = None
        self.collection_name = COLLECTION_NAME
        self.vector_dim = VECTOR_DIM

    @property
    def client(self):
        if self._client is None:
            print(f"[QDRANT] 连接 {QDRANT_HOST}:{QDRANT_PORT}")
            self._client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        return self._client

    def ensure_collection(self):
        exist = self.client.collection_exists(self.collection_name)
        if not exist:
            print(f"[QDRANT] 创建集合 {self.collection_name}, dim={self.vector_dim}")
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
            limit=TOP_K
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