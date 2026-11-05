import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator, Dict, List

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
        self._rewrite_prompt = None

    def _load_prompt(self, name: str) -> str:
        return (Path(settings.prompt_dir) / name).read_text(encoding="utf-8")

    @property
    def system_prompt(self) -> str:
        # prompt_reload_on_each_call=True 时每次请求重读文件，实现改提示词免重启
        if self._system_prompt is None or settings.prompt_reload_on_each_call:
            self._system_prompt = self._load_prompt("system.md")
        return self._system_prompt

    @property
    def rewrite_prompt(self) -> str:
        if self._rewrite_prompt is None or settings.prompt_reload_on_each_call:
            self._rewrite_prompt = self._load_prompt("rewrite.md")
        return self._rewrite_prompt

    def _backoff(self, attempt: int) -> float:
        # 指数退避：基数 * 2^attempt，即 2s、4s、8s…
        return self.retry_delay_base * (2 ** attempt)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            # connect 短超时（网关不可达要快速失败），read 用配置的宽松超时（生成慢）
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=self.timeout, write=10.0, pool=10.0)
            )
        return self._client

    def _payload(self, messages: List[Dict], temperature: float, stream: bool) -> dict:
        return {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": stream
        }

    async def _chat_raw(self, messages: List[Dict], temperature: float) -> str:
        """非流式调用，messages 必须已含 system。带指数退避重试。"""
        payload = self._payload(messages, temperature, stream=False)
        for attempt in range(self.max_retry):
            try:
                resp = await self.client.post(self.api_url, headers=self.headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                if attempt == self.max_retry - 1:
                    raise e
                await asyncio.sleep(self._backoff(attempt))

    async def _stream_raw(self, messages: List[Dict], temperature: float) -> AsyncGenerator[str, None]:
        """流式调用，messages 必须已含 system。带指数退避重试。"""
        payload = self._payload(messages, temperature, stream=True)
        for attempt in range(self.max_retry):
            try:
                async with self.client.stream("POST", self.api_url, headers=self.headers, json=payload) as resp:
                    resp.raise_for_status()
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

    async def chat(self, messages: List[Dict], temperature: float = 0.1) -> str:
        """多轮对话：messages 是历史 + 当前 user 消息（不含 system），内部自动前置 system。"""
        full = [{"role": "system", "content": self.system_prompt}] + messages
        return await self._chat_raw(full, temperature)

    async def stream_chat(self, messages: List[Dict], temperature: float = 0.1) -> AsyncGenerator[str, None]:
        full = [{"role": "system", "content": self.system_prompt}] + messages
        async for delta in self._stream_raw(full, temperature):
            yield delta

    async def rewrite_query(self, history: List[Dict], question: str) -> str:
        """把追问改写成不依赖上下文的独立检索查询（多轮 RAG 的关键一步）。"""
        full = [{"role": "system", "content": self.rewrite_prompt}]
        full.extend(history)
        full.append({"role": "user", "content": f"当前追问：{question}"})
        return (await self._chat_raw(full, temperature=0.0)).strip()


llm_client = LLMClient()
