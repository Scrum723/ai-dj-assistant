"""Priority queue for Halo replies — operator > safety > supporter > … > idle."""

from __future__ import annotations

import heapq
import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DEFAULT_PRIORITIES = {
    "operator": 0,
    "safety": 1,
    "supporter": 2,
    "request": 3,
    "mention": 4,
    "first_timer": 5,
    "regular": 6,
    "idle": 9,
}

# Priorities at or above this expire after task_ttl_sec (keep paid/safety forever)
DEFAULT_EXPIRE_FROM = 5  # first_timer and lower-urgency

ACK_WAIT_SEC = 20.0


@dataclass(order=True)
class _HeapItem:
    priority: int
    created_ts: float
    seq: int
    task: Any = field(compare=False)


@dataclass
class HaloTask:
    priority: int
    created_ts: float
    expires_ts: float
    kind: str
    target: str
    platform: str
    payload: dict
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    acked: bool = False

    def to_public(self, position: int | None = None, now: float | None = None) -> dict:
        ts = time.time() if now is None else float(now)
        out = {
            "id": self.task_id,
            "priority": self.priority,
            "kind": self.kind,
            "target": self.target,
            "platform": self.platform,
            "created_ts": self.created_ts,
            "expires_ts": self.expires_ts,
            "acked": self.acked,
            "wait_sec": max(0.0, ts - self.created_ts),
        }
        if position is not None:
            out["position"] = position
        return out


class PriorityQueue:
    """Thread-safe priority queue of HaloTask items."""

    def __init__(
        self,
        *,
        priorities: Optional[dict[str, int]] = None,
        task_ttl_sec: float = 120.0,
        expire_from_priority: int = DEFAULT_EXPIRE_FROM,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._priorities = dict(DEFAULT_PRIORITIES)
        if priorities:
            self._priorities.update({str(k): int(v) for k, v in priorities.items()})
        self.task_ttl_sec = float(task_ttl_sec)
        self.expire_from_priority = int(expire_from_priority)
        self._clock = clock or time.time
        self._heap: list[_HeapItem] = []
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._by_id: dict[str, HaloTask] = {}

    def configure(
        self,
        *,
        priorities: Optional[dict[str, int]] = None,
        task_ttl_sec: Optional[float] = None,
        expire_from_priority: Optional[int] = None,
    ) -> None:
        with self._lock:
            if priorities:
                self._priorities.update({str(k): int(v) for k, v in priorities.items()})
            if task_ttl_sec is not None:
                self.task_ttl_sec = float(task_ttl_sec)
            if expire_from_priority is not None:
                self.expire_from_priority = int(expire_from_priority)

    def priority_for(self, kind: str) -> int:
        key = (kind or "regular").strip().lower()
        if key in self._priorities:
            return int(self._priorities[key])
        # aliases
        aliases = {
            "super_chat": "supporter",
            "gift": "supporter",
            "member": "supporter",
            "follow": "supporter",
            "welcome": "first_timer",
            "new_viewer": "first_timer",
            "returning": "regular",
            "question": "regular",
            "hype": "regular",
            "chat": "regular",
            "mod": "safety",
            "moderation": "safety",
        }
        mapped = aliases.get(key, "regular")
        return int(self._priorities.get(mapped, DEFAULT_PRIORITIES["regular"]))

    def push(
        self,
        *,
        kind: str,
        target: str,
        platform: str = "chat",
        payload: Optional[dict] = None,
        priority: Optional[int] = None,
        now: Optional[float] = None,
    ) -> HaloTask:
        now = float(self._clock() if now is None else now)
        pri = int(self.priority_for(kind) if priority is None else priority)
        if pri >= self.expire_from_priority:
            expires = now + self.task_ttl_sec
        else:
            expires = float("inf")
        task = HaloTask(
            priority=pri,
            created_ts=now,
            expires_ts=expires,
            kind=(kind or "regular").strip().lower(),
            target=(target or "Fan").strip()[:40],
            platform=(platform or "chat").strip().lower() or "chat",
            payload=dict(payload or {}),
        )
        with self._lock:
            heapq.heappush(
                self._heap,
                _HeapItem(priority=pri, created_ts=now, seq=next(self._seq), task=task),
            )
            self._by_id[task.task_id] = task
        return task

    def expire(self, now: Optional[float] = None) -> list[HaloTask]:
        """Drop expired low-priority tasks. Returns removed tasks."""
        now = float(self._clock() if now is None else now)
        removed: list[HaloTask] = []
        with self._lock:
            survivors: list[_HeapItem] = []
            for item in self._heap:
                t = item.task
                if t.expires_ts <= now:
                    removed.append(t)
                    self._by_id.pop(t.task_id, None)
                else:
                    survivors.append(item)
            if removed:
                heapq.heapify(survivors)
                self._heap = survivors
        return removed

    def _alive_sorted(self) -> list[HaloTask]:
        items = sorted(self._heap, key=lambda h: (h.priority, h.created_ts, h.seq))
        return [h.task for h in items]

    def length(self) -> int:
        with self._lock:
            return len(self._heap)

    def position(self, task_id: str) -> Optional[int]:
        """1-based position in priority order, or None if missing."""
        with self._lock:
            for i, t in enumerate(self._alive_sorted(), start=1):
                if t.task_id == task_id:
                    return i
        return None

    def position_for_target(self, target: str) -> Optional[int]:
        key = (target or "").strip().lower()
        if not key:
            return None
        with self._lock:
            for i, t in enumerate(self._alive_sorted(), start=1):
                if t.target.lower() == key:
                    return i
        return None

    def peek(self) -> Optional[HaloTask]:
        with self._lock:
            alive = self._alive_sorted()
            return alive[0] if alive else None

    def pop(
        self,
        *,
        now: Optional[float] = None,
        skip_user: Optional[Callable[[HaloTask], bool]] = None,
    ) -> Optional[HaloTask]:
        """Pop highest-priority non-expired task. skip_user(task)->True means on cooldown."""
        now = float(self._clock() if now is None else now)
        self.expire(now)
        with self._lock:
            kept: list[_HeapItem] = []
            chosen: Optional[HaloTask] = None
            while self._heap:
                item = heapq.heappop(self._heap)
                t = item.task
                if t.expires_ts <= now:
                    self._by_id.pop(t.task_id, None)
                    continue
                if skip_user and skip_user(t):
                    kept.append(item)
                    continue
                chosen = t
                self._by_id.pop(t.task_id, None)
                break
            for item in kept:
                heapq.heappush(self._heap, item)
            return chosen

    def tasks_needing_ack(self, now: Optional[float] = None, wait_sec: float = ACK_WAIT_SEC) -> list[tuple[HaloTask, int]]:
        """Tasks waiting longer than wait_sec that have not been acked yet."""
        now = float(self._clock() if now is None else now)
        out: list[tuple[HaloTask, int]] = []
        with self._lock:
            for i, t in enumerate(self._alive_sorted(), start=1):
                if t.acked:
                    continue
                if now - t.created_ts >= wait_sec:
                    out.append((t, i))
        return out

    def mark_acked(self, task_id: str) -> bool:
        with self._lock:
            t = self._by_id.get(task_id)
            if not t:
                return False
            t.acked = True
            return True

    def clear(self) -> None:
        with self._lock:
            self._heap.clear()
            self._by_id.clear()

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        self.expire(now)
        with self._lock:
            tasks = self._alive_sorted()
            public = [t.to_public(position=i, now=now) for i, t in enumerate(tasks, start=1)]
            positions = {t.target.lower(): i for i, t in enumerate(tasks, start=1)}
            return {
                "length": len(tasks),
                "tasks": public[:20],
                "positions": positions,
            }
