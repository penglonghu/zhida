import asyncio
import logging
import tempfile
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from schemas import QueryRequest, QueryResponse, StreamQueryRequest, UploadResponse, ClearResponse, ChunkListResponse
from core.llm import llm_client
from core.rag_retriever import retriever
from core.doc_parser import parse_and_chunk
from fastapi.middleware.cors import CORSMiddleware
from config import settings
from core.logging import setup_logging

setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="知答 ZhiDa", version="0.1.0")


@app.post("/query", response_model=QueryResponse, summary="同步问答接口（RAG增强）")
async def chat(req: QueryRequest):
    logger.info(f"收到同步请求 question={req.question}, doc_ids={req.doc_ids}, temperature={req.temperature}")
    try:
        # RAG检索：本地 embedding + Qdrant 是阻塞调用，放线程池避免卡住事件循环
        context_list = await asyncio.to_thread(retriever.search, req.question)
        context_str = "\n".join(context_list)

        # 拼接参考资料上下文
        if context_str:
            user_msg = f"""【参考资料】
{context_str}

【用户问题】
{req.question}
"""
        else:
            user_msg = req.question

        answer = await llm_client.chat(user_query=user_msg, temperature=req.temperature)
        resp = QueryResponse(
            answer=answer,
            question=req.question,
            doc_ids=req.doc_ids,
            success=True,
            msg="ok"
        )
        logger.info("同步请求处理成功")
        return resp
    except Exception as e:
        logger.exception("同步请求异常")
        raise HTTPException(status_code=500, detail=f"服务异常：{str(e)}")


@app.post("/stream-query", summary="SSE流式问答接口（RAG增强，演示用）")
async def stream_chat(req: StreamQueryRequest):
    logger.info(f"收到流式请求 question={req.question}, temperature={req.temperature}")
    # RAG检索上下文（阻塞调用，放线程池）
    context_list = await asyncio.to_thread(retriever.search, req.question)
    context_str = "\n".join(context_list)

    if context_str:
        user_msg = f"""【参考资料】
{context_str}

【用户问题】
{req.question}
"""
    else:
        user_msg = req.question

    async def gen():
        async for chunk in llm_client.stream_chat(user_query=user_msg, temperature=req.temperature):
            # 保留标准SSE格式
            yield f"data: {chunk}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/upload-doc", response_model=UploadResponse, summary="上传文档入库（pdf/txt/md）")
async def upload_doc(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    logger.info(f"收到文档上传：filename={file.filename}")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in [".pdf", ".txt", ".md"]:
        logger.warning(f"不支持文件类型：{suffix}")
        raise HTTPException(status_code=400, detail="仅支持 pdf / txt / md 文件")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)
        chunks = parse_and_chunk(tmp_path)
        cnt = retriever.add_chunks(chunks)
        tmp_path.unlink()
        logger.info(f"文档入库完成，生成chunk数量：{cnt}")
        return UploadResponse(msg="文件入库成功", chunk_count=cnt)
    except Exception as e:
        logger.exception("文档上传入库失败")
        raise HTTPException(status_code=500, detail=f"文档处理失败：{str(e)}")


@app.post("/clear-collection", response_model=ClearResponse, summary="清空向量知识库")
def clear_collection():
    logger.info("执行清空向量库操作")
    try:
        retriever.clear()
        return ClearResponse(msg="向量库已清空")
    except Exception as e:
        logger.exception("清空向量库失败")
        raise HTTPException(status_code=500, detail=f"清空失败：{str(e)}")


@app.get("/list-chunks", response_model=ChunkListResponse, summary="调试接口：查看全部文本块")
def list_chunks():
    logger.info("查询全部chunk列表")
    try:
        chunks = retriever.list_all_chunks()
        return ChunkListResponse(chunks=chunks)
    except Exception as e:
        logger.exception("获取chunk列表失败")
        raise HTTPException(status_code=500, detail=f"查询失败：{str(e)}")


@app.get("/health", summary="健康检查（客户监控必备）")
def health():
    return {"status": "ok", "name": "知答 ZhiDa", "version": "0.1.0"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.service_host,
        port=settings.service_port
    )