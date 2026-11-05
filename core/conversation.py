"""多轮会话存储：按 session_id 记住问答历史，供 LLM 理解上下文。

POC 版存进程内存：重启即丢、多 worker 不共享。
S4/S5 上线前要换成 Redis（或数据库），否则多实例会串会话、也无法审计。
"""
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


class ConversationStore:
    def __init__(self, max_turns: int = None, ttl_seconds: int = None):
        self.max_turns = max_turns if max_turns is not None else settings.conversation_max_turns
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.conversation_ttl_seconds
        self._store: Dict[str, Conversation] = {}

    def _expire(self) -> None:
        """惰性清理空闲超时的会话，避免内存无限增长。"""
        now = time.time()
        expired = [sid for sid, c in self._store.items() if now - c.updated_at > self.ttl_seconds]
        for sid in expired:
            del self._store[sid]

    def get_history(self, session_id: str) -> List[dict]:
        """返回 OpenAI messages 格式的会话历史（不含 system）。"""
        c = self._store.get(session_id)
        if c is None:
            return []
        msgs: List[dict] = []
        for turn in c.turns:
            msgs.append({"role": "user", "content": turn["question"]})
            msgs.append({"role": "assistant", "content": turn["answer"]})
        return msgs

    def append(self, session_id: str, question: str, answer: str) -> None:
        c = self._store.get(session_id)
        if c is None:
            c = Conversation(session_id=session_id)
            self._store[session_id] = c
        c.turns.append({"question": question, "answer": answer})
        if len(c.turns) > self.max_turns:
            c.turns = c.turns[-self.max_turns:]   # 只保留最近 N 轮，控制 token 预算
        c.updated_at = time.time()
        self._expire()

    def clear(self, session_id: str) -> bool:
        return self._store.pop(session_id, None) is not None


conversation_store = ConversationStore()
