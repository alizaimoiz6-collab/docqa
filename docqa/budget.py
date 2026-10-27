"""Daily spend cap shared by every visitor and every container."""

import datetime as dt
import os
import threading
import uuid
from collections import defaultdict
from typing import Callable, Protocol

from .schemas import Usage

# USD per million tokens. Cache writes use the 5-minute TTL (1.25x input).
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
# A refusal fallback can answer on a model not listed above; price it at the
# most expensive tier so the budget errs on the side of stopping early.
FALLBACK_PRICE = {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50}


def usage_cost(model: str, usage: Usage) -> float:
    p = PRICES.get(model, FALLBACK_PRICE)
    return (
        usage.input_tokens * p["input"]
        + usage.output_tokens * p["output"]
        + usage.cache_read_input_tokens * p["cache_read"]
        + usage.cache_creation_input_tokens * p["cache_write"]
    ) / 1e6


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def seconds_until_utc_midnight() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    tomorrow = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), dt.timezone.utc)
    return int((tomorrow - now).total_seconds()) + 1


class SpendLedger(Protocol):
    def spent_today(self) -> float: ...
    def add(self, amount: float) -> None: ...


class InMemoryLedger:
    def __init__(self, today: Callable[[], dt.date] = utc_today):
        self._today = today
        self._spent: dict[dt.date, float] = defaultdict(float)
        self._lock = threading.Lock()

    def spent_today(self) -> float:
        return self._spent[self._today()]

    def add(self, amount: float) -> None:
        with self._lock:
            self._spent[self._today()] += amount


class ModalDictLedger:
    """Each container writes only its own key, so concurrent containers never
    overwrite each other's totals; the day's spend is the sum across keys."""

    def __init__(self, name: str = "docqa-budget", today: Callable[[], dt.date] = utc_today):
        import modal

        self._dict = modal.Dict.from_name(name, create_if_missing=True)
        self._today = today
        self._container = os.environ.get("MODAL_TASK_ID") or uuid.uuid4().hex
        self._local: dict[dt.date, float] = defaultdict(float)
        self._lock = threading.Lock()

    def spent_today(self) -> float:
        prefix = f"{self._today().isoformat()}:"
        return sum(v for k, v in self._dict.items() if k.startswith(prefix))

    def add(self, amount: float) -> None:
        with self._lock:
            day = self._today()
            self._local[day] += amount
            self._dict[f"{day.isoformat()}:{self._container}"] = self._local[day]


def make_ledger(backend: str) -> SpendLedger:
    if backend == "modal":
        return ModalDictLedger()
    return InMemoryLedger()
