# -*- coding: utf-8 -*-
"""新条件匹配链路的线程安全内容缓存。

缓存保存已校验的模型原始输出。时效分仍在每次请求时由服务端复算，避免跨日期
命中时把旧的申报窗口分数带入新结果。
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from threading import Event, RLock
from typing import Any, Dict, Optional, Tuple


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    waits: int = 0
    evictions: int = 0


class MatchCache:
    """带 in-flight 去重的内存 LRU 缓存。"""

    def __init__(self, maximum_entries: int = 2000) -> None:
        self.maximum_entries = max(int(maximum_entries), 1)
        self._records: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._inflight: Dict[str, Event] = {}
        self._lock = RLock()
        self._stats = CacheStats()

    def acquire(self, key: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[Event]]:
        """返回 hit、owner 或 wait。owner 负责随后调用 complete/fail。"""
        with self._lock:
            if key in self._records:
                self._records.move_to_end(key)
                self._stats.hits += 1
                return "hit", deepcopy(self._records[key]), None
            event = self._inflight.get(key)
            if event is not None:
                self._stats.waits += 1
                return "wait", None, event
            event = Event()
            self._inflight[key] = event
            self._stats.misses += 1
            return "owner", None, event

    def complete(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            self._records[key] = deepcopy(value)
            self._records.move_to_end(key)
            while len(self._records) > self.maximum_entries:
                self._records.popitem(last=False)
                self._stats.evictions += 1
            event = self._inflight.pop(key, None)
            if event:
                event.set()

    def fail(self, key: str) -> None:
        with self._lock:
            event = self._inflight.pop(key, None)
            if event:
                event.set()

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._inflight.clear()
            self._stats = CacheStats()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._records),
                "inflight": len(self._inflight),
                "maximumEntries": self.maximum_entries,
                "hits": self._stats.hits,
                "misses": self._stats.misses,
                "waits": self._stats.waits,
                "evictions": self._stats.evictions,
            }


_CACHE: Optional[MatchCache] = None
_CACHE_SIZE: Optional[int] = None


def get_match_cache(maximum_entries: int = 2000) -> MatchCache:
    global _CACHE, _CACHE_SIZE
    if _CACHE is None or _CACHE_SIZE != maximum_entries:
        _CACHE = MatchCache(maximum_entries)
        _CACHE_SIZE = maximum_entries
    return _CACHE


def reset_match_cache() -> None:
    global _CACHE, _CACHE_SIZE
    _CACHE = None
    _CACHE_SIZE = None
