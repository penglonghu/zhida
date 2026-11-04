import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def stream_chat(question: str):
    url = os.getenv("LLM_API_URL")
    headers = {
        "Authorization": f"Bearer {os.getenv('LLM_API_KEY')}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "xxx",
        "messages": [{"role": "user", "content": question}],
        "stream": True,
        "temperature": 0.1
    }
    try:
        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=int(os.getenv("LLM_TIMEOUT", 60))
        )
        resp.raise_for_status()
        full_text = ""
        for chunk in resp.iter_content(chunk_size=1024, decode_unicode=True):
            if chunk:
                full_text += chunk
                print(chunk, end="")
        logger.info(f"流式输出完成，总长度：{len(full_text)}")
        return full_text
    except requests.exceptions.Timeout:
        logger.error("请求超时！内网环境非常容易遇到")
    except requests.exceptions.ConnectionError:
        logger.error("连接失败，检查网络/代理/内网连通性")
    except Exception as e:
        logger.exception("调用LLM失败")

if __name__ == "__main__":
    stream_chat("简单介绍RAG")