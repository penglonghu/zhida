from pydantic import BaseModel, Field
from typing import List, Optional

# ===== 问答schema =====
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, description="用户提问，不能为空")
    session_id: Optional[str] = Field(default=None, description="会话ID，不传则新开会话")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0, description="模型温度，RAG场景一般偏低")

class StreamQueryRequest(BaseModel):
    question: str = Field(..., min_length=2)
    session_id: Optional[str] = Field(default=None, description="会话ID，不传则新开会话")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)

class QueryResponse(BaseModel):
    answer: str
    question: str
    session_id: Optional[str] = None
    success: bool
    msg: str

# ===== RAG相关 =====
class UploadResponse(BaseModel):
    msg: str
    chunk_count: int

class ClearResponse(BaseModel):
    msg: str

class SessionClearRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="要清除的会话ID")

class ChunkListResponse(BaseModel):
    chunks: List[str]
