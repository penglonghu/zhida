import os
from typing import List
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

class EmbeddingClient:
    def __init__(self):
        self.model_name = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-small-zh")
        self.device = os.getenv("EMBED_DEVICE", "auto")
        self._model = None  # 先不加载

    @property
    def model(self):
        if self._model is None:
            print(f"[EMBED] 开始加载模型: {self.model_name}")
            self._model = SentenceTransformer(self.model_name, device=self.device)
            print(f"[EMBED] 模型加载完成")
        return self._model

    def encode(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def encode_single(self, text: str) -> List[float]:
        return self.encode([text])[0]

embedding_client = EmbeddingClient()