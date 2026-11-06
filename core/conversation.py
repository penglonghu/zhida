"""多轮会话存储：按 (tenant_id, session_id) 记住问答历史，供 LLM 理解上下文。

两种后端，通过 config.redis_enabled 切换：
- MemoryConversationStore：进程内存 dict，单实例够用，重启即丢、多 worker 不共享。
- RedisConversationStore：外部 Redis 共享，多 worker/多实例读写同一份历史。

接口统一为 async（FastAPI 事件循环内不阻塞），main.py 用 await 调用。
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class Conversation:
    session_id: str
    turns: List[dict] = field(default_factory=list)   # [{"question": ..., "answer": ...}, ...]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class MemoryConversationStore:
    """进程内存实现：单实例可用，重启即丢、多 worker 不共享。"""

    def __init__(self, max_turns: int = None, ttl_seconds: int = None):
        self.max_turns = max_turns if max_turns is not None else settings.conversation_max_turns
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.conversation_ttl_seconds
        self._store: Dict[str, Conversation] = {}

    def _expire(self) -> None:
        """惰性清理空闲超时的会话，避免内存无限增长。"""
        now = time.time()
        expired = [k for k, c in self._store.items() if now - c.updated_at > self.ttl_seconds]
        for k in expired:
            del self._store[k]

    @staticmethod
    def _key(tenant_id: str, session_id: str) -> str:
        # 会话按租户隔离：即使猜到别人的 session_id，也读不到其历史
        return f"{tenant_id}:{session_id}"

    async def get_history(self, tenant_id: str, session_id: str) -> List[dict]:
        """返回 OpenAI messages 格式的会话历史（不含 system）。"""
        c = self._store.get(self._key(tenant_id, session_id))
        if c is None:
            return []
        msgs: List[dict] = []
        for turn in c.turns:
            msgs.append({"role": "user", "content": turn["question"]})
            msgs.append({"role": "assistant", "content": turn["answer"]})
        return msgs

    async def append(self, tenant_id: str, session_id: str, question: str, answer: str) -> None:
        key = self._key(tenant_id, session_id)
        c = self._store.get(key)
        if c is None:
            c = Conversation(session_id=session_id)
            self._store[key] = c
        c.turns.append({"question": question, "answer": answer})
        if len(c.turns) > self.max_turns:
            c.turns = c.turns[-self.max_turns:]   # 只保留最近 N 轮，控制 token 预算
        c.updated_at = time.time()
        self._expire()

    async def clear(self, tenant_id: str, session_id: str) -> bool:
        return self._store.pop(self._key(tenant_id, session_id), None) is not None


class RedisConversationStore:
    """Redis 实现：所有实例共享同一份会话历史。

    value 存 JSON 数组 [{"question","answer"}, ...]；TTL 用 Redis 原生
    EXPIRE，每次 append 续期，取代内存版的惰性过期。
    """

    # 原子 append：读-改-写若拆成 GET + SET 两条命令，多实例并发会丢更新
    # （A、B 都读到旧值 → A 写 → B 写覆盖 A 的新轮）。用 Lua 在 Redis 服务端
    # 单线程内一次完成「读 + 追加 + 截断 + 写 + 续期」，天然无竞态。
    _APPEND_LUA = """
    local raw = redis.call('GET', KEYS[1])
    local turns = {}
    if raw then
        turns = cjson.decode(raw)
    end
    turns[#turns + 1] = cjson.decode(ARGV[1])
    local max_turns = tonumber(ARGV[2])
    if #turns > max_turns then
        local keep = {}
        for i = #turns - max_turns + 1, #turns do
            keep[#keep + 1] = turns[i]
        end
        turns = keep
    end
    redis.call('SET', KEYS[1], cjson.encode(turns))
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
    return 1
    """

    def __init__(self, max_turns: int = None, ttl_seconds: int = None):
        from core.redis_client import get_redis

        self.max_turns = max_turns if max_turns is not None else settings.conversation_max_turns
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.conversation_ttl_seconds
        self._redis = get_redis()
        self._append_script = self._redis.register_script(self._APPEND_LUA)

    def _key(self, tenant_id: str, session_id: str) -> str:
        return f"{settings.redis_prefix}:{tenant_id}:session:{session_id}"

    async def get_history(self, tenant_id: str, session_id: str) -> List[dict]:
        raw = await self._redis.get(self._key(tenant_id, session_id))
        if not raw:
            return []
        turns = json.loads(raw)
        msgs: List[dict] = []
        for turn in turns:
            msgs.append({"role": "user", "content": turn["question"]})
            msgs.append({"role": "assistant", "content": turn["answer"]})
        return msgs

    async def append(self, tenant_id: str, session_id: str, question: str, answer: str) -> None:
        turn = json.dumps({"question": question, "answer": answer}, ensure_ascii=False)
        await self._append_script(
            keys=[self._key(tenant_id, session_id)],
            args=[turn, self.max_turns, self.ttl_seconds],
        )

    async def clear(self, tenant_id: str, session_id: str) -> bool:
        return bool(await self._redis.delete(self._key(tenant_id, session_id)))


conversation_store = (
    RedisConversationStore() if settings.redis_enabled else MemoryConversationStore()
)
