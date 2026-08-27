"""会话状态：知行执行层的跨轮状态机（槽位收集 / 确认）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

TTL_SECONDS = 30 * 60


@dataclass
class TxSession:
    session_id: str
    role: str = "student"
    user: str = ""
    phase: str = "idle"  # idle | collect | confirm
    tool: str = ""
    slots: dict = field(default_factory=dict)
    last_asked: str = ""
    updated: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated = time.time()


class SessionStore:
    def __init__(self, ttl: int = TTL_SECONDS):
        self.ttl = ttl
        self._data: dict[str, TxSession] = {}

    def _evict(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._data.items() if now - s.updated > self.ttl]
        for sid in stale:
            del self._data[sid]

    def get(self, session_id: str) -> TxSession | None:
        self._evict()
        return self._data.get(session_id)

    def ensure(self, session_id: str, role: str, user: str) -> TxSession:
        self._evict()
        sess = self._data.get(session_id)
        if sess is None:
            sess = TxSession(session_id=session_id, role=role, user=user)
            self._data[session_id] = sess
        sess.role, sess.user = role, user
        sess.touch()
        return sess

    def clear(self, session_id: str) -> None:
        self._data.pop(session_id, None)


sessions = SessionStore()
