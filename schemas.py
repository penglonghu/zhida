from pydantic import BaseModel, Field
from typing import List, Optional

# ===== 问答schema =====
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, description="用户提问，不能为空")
    session_id: Optional[str] = Field(default=None, description="会话ID，不传则新开会话")
    kb_id: Optional[str] = Field(default=None, description="知识库ID，默认 default")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0, description="模型温度，RAG场景一般偏低")

class StreamQueryRequest(BaseModel):
    question: str = Field(..., min_length=2)
    session_id: Optional[str] = Field(default=None, description="会话ID，不传则新开会话")
    kb_id: Optional[str] = Field(default=None, description="知识库ID，默认 default")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)

class Source(BaseModel):
    """一条引用来源：编号对应答案里 [n] 的 n，附文档名与原文片段。"""
    index: int
    doc_name: str
    chunk_index: int
    text: str

class QueryResponse(BaseModel):
    answer: str
    question: str
    session_id: Optional[str] = None
    success: bool
    msg: str
    sources: List[Source] = []
    removed_citations: List[int] = []   # 引用校验即修正：已从答案移除的无依据 [n] 编号列表（citation_verify_enabled=true 时才有）

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
