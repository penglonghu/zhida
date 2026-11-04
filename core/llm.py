import asyncio
import json
from pathlib import Path

import httpx

from config import settings


class LLMClient:
    def __init__(self):
        self.api_url = settings.llm_api_url
        self.api_key = settings.llm_api_key
        self.model_name = settings.llm_model_name
        self.timeout = settings.llm_timeout / 1000.0
        self.max_retry = settings.llm_max_retry
        self.retry_delay_base = settings.llm_retry_delay_base
        self._client = None

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        self._system_prompt = None

    @property
    def system_prompt(self) -> str:
        # prompt_reload_on_each_call=True 时每次请求重读文件，实现改提示词免重启
        if self._system_prompt is None or settings.prompt_reload_on_each_call:
            path = Path(settings.prompt_dir) / "system.md"
            self._system_prompt = path.read_text(encoding="utf-8")
        return self._system_prompt

    def _backoff(self, attempt: int) -> float:
        # 指数退避：基数 * 2^attempt，即 2s、4s、8s…
        return self.retry_delay_base * (2 ** attempt)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def chat(self, user_query: str, temperature: float = 0.1) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_query}
            ],
            "temperature": temperature,
            "stream": False
        }
        for attempt in range(self.max_retry):
            try:
                resp = await self.client.post(
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                if attempt == self.max_retry - 1:
                    raise e
                await asyncio.sleep(self._backoff(attempt))

    async def stream_chat(self, user_query: str, temperature: float = 0.1):
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_query}
            ],
            "temperature": temperature,
            "stream": True
        }
        for attempt in range(self.max_retry):
            try:
                async with self.client.stream(
                    "POST",
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    # SSE解析
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            chunk_data = line[6:]
                            if chunk_data == "[DONE]":
                                break
                            obj = json.loads(chunk_data)
                            delta = obj["choices"][0]["delta"].get("content")
                            if delta:
                                yield delta
                return
            except Exception as e:
                if attempt == self.max_retry - 1:
                    raise e
                await asyncio.sleep(self._backoff(attempt))


llm_client = LLMClient()