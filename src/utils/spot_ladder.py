"""Fargate Spot price ladder (task 1.1b, owner 2026-11-18).

Owner rule: finish the work at the lowest price available at that moment, and NEVER above the
on-demand hourly price of the task's ORIGINAL size (critical). A task starts on Fargate Spot at its
task-definition size (rung 0). Each time a book's task is reclaimed, the next launch for that book
moves up one rung: double the vCPU and memory, still on Spot, while that size's Spot price stays at
or below the original size's on-demand price (x ``max_fraction``). When the next rung would cost
more - or the rates are unknown or stale, or the size is not a valid Fargate size - the next launch
is ON-DEMAND at the ORIGINAL size. A success resets the book's ladder.

Prices are re-checked at EVERY launch (src/utils/fargate_prices), so a rung that has become too
expensive since the reclaim launches on-demand at the original size instead. The Spot rate is the
highest daily rate actually charged in the last 7 days (owner choice: Cost Explorer), so the check
cannot see a Spot price rise since the last day Spot tasks ran; today Spot is ~36% of on-demand,
and rung 1 (double size) stays under the limit until Spot passes 50%. A margin can be set with
``spot_ladder.max_fraction_of_on_demand`` (below 1.0).

State: one row per task family and book, ``spot#<family>#<book>`` in the api-cache table, holding
the rung, the original size (taken from the first reclaimed task, which ran at rung 0) and the
reclaim count.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from src.utils.fargate_prices import Rates

ONDEMAND = "ondemand"
SPOT = "FARGATE_SPOT"
FARGATE = "FARGATE"
STATE_TTL_DAYS = 30
RECENT_TASKS = (
    10  # reclaims already counted: a retried report of any of them is not counted again
)
DEFAULT_MAX_FRACTION = 1.0

Rung = Union[int, str]

# Valid Fargate (Linux) CPU units -> allowed memory MB (min, max, step).
_FARGATE_SIZES = {
    256: (512, 2048, None),  # 512, 1024, 2048
    512: (1024, 4096, 1024),
    1024: (2048, 8192, 1024),
    2048: (4096, 16384, 1024),
    4096: (8192, 30720, 1024),
    8192: (16384, 61440, 4096),
    16384: (32768, 122880, 8192),
}


def valid_size(cpu: int, memory: int) -> bool:
    """True if (cpu units, memory MB) is a valid Fargate Linux task size."""
    if cpu not in _FARGATE_SIZES:
        return False
    low, high, step = _FARGATE_SIZES[cpu]
    if step is None:
        return memory in (512, 1024, 2048)
    return low <= memory <= high and (memory - low) % step == 0


@dataclass(frozen=True)
class Launch:
    """How to launch: capacity provider, task size, the rung, and the hourly price if known."""

    capacity: str
    cpu: int
    memory: int
    rung: Rung
    hourly: Optional[float] = None

    @property
    def overridden(self) -> bool:
        return self.rung not in (0, ONDEMAND)

    def describe(self) -> str:
        kind = "Spot" if self.capacity == SPOT else "on-demand"
        price = f", ${self.hourly:.4f}/h" if self.hourly is not None else ""
        return f"{kind} {self.cpu / 1024:g} vCPU / {self.memory / 1024:g} GB{price}"


def _limit(base_cpu: int, base_mem: int, rates: Rates, max_fraction: float) -> float:
    return rates.hourly(base_cpu, base_mem, spot=False) * max_fraction


def spot_rung_allowed(
    base_cpu: int,
    base_mem: int,
    rung: int,
    rates: Optional[Rates],
    max_fraction: float = DEFAULT_MAX_FRACTION,
) -> bool:
    """True if Spot at ``rung`` (base x 2**rung) is a valid size and at or under the limit. Rung 0
    with unknown rates is allowed: Fargate Spot is priced below on-demand for the same size.
    """
    cpu, mem = base_cpu * 2**rung, base_mem * 2**rung
    if not valid_size(cpu, mem):
        return False
    if rates is None:
        return rung == 0
    return rates.hourly(cpu, mem, spot=True) <= _limit(
        base_cpu, base_mem, rates, max_fraction
    )


def launch_for(
    base_cpu: int,
    base_mem: int,
    rung: Rung,
    rates: Optional[Rates],
    max_fraction: float = DEFAULT_MAX_FRACTION,
) -> Launch:
    """The launch for ``rung``, re-checked against the current rates."""
    if rung != ONDEMAND and spot_rung_allowed(
        base_cpu, base_mem, int(rung), rates, max_fraction
    ):
        k = int(rung)
        cpu, mem = base_cpu * 2**k, base_mem * 2**k
        price = rates.hourly(cpu, mem, spot=True) if rates else None
        return Launch(SPOT, cpu, mem, k, price)
    price = rates.hourly(base_cpu, base_mem, spot=False) if rates else None
    return Launch(FARGATE, base_cpu, base_mem, ONDEMAND, price)


def next_rung(
    base_cpu: int,
    base_mem: int,
    rung: Rung,
    rates: Optional[Rates],
    max_fraction: float = DEFAULT_MAX_FRACTION,
) -> Rung:
    """The rung after a reclaim at ``rung``."""
    if rung == ONDEMAND:
        return ONDEMAND
    k = int(rung) + 1
    return (
        k if spot_rung_allowed(base_cpu, base_mem, k, rates, max_fraction) else ONDEMAND
    )


def state_key(family: str, book: str) -> str:
    if "#" in family or "#" in (book or ""):
        raise ValueError("family and book must not contain '#'")
    return f"spot#{family}#{book or '_all'}"


def get_state(table: Any, family: str, book: str) -> Optional[Dict[str, Any]]:
    """The book's ladder row: {rung, base_cpu, base_mem, reclaims, recent_tasks}, or None."""
    item = table.get_item(Key={"cache_key": state_key(family, book)}).get("Item")
    if not item:
        return None
    rung = item.get("rung", 0)
    return {
        "rung": ONDEMAND if rung == ONDEMAND else int(rung),
        "base_cpu": int(item["base_cpu"]),
        "base_mem": int(item["base_mem"]),
        "reclaims": int(item.get("reclaims", 0)),
        "recent_tasks": [str(t) for t in item.get("recent_tasks") or []],
    }


BASE_ENV = "SPOT_LADDER_BASE"


def base_env_value(base_cpu: int, base_mem: int) -> str:
    """The SPOT_LADDER_BASE container env value for an upsized launch: its original size."""
    return f"{int(base_cpu)}x{int(base_mem)}"


def parse_base_env(value: str) -> Optional[tuple]:
    """(cpu, memory) from a SPOT_LADDER_BASE value, or None if absent or not a valid size."""
    try:
        cpu, mem = (int(x) for x in str(value).split("x", 1))
    except (TypeError, ValueError):
        return None
    return (cpu, mem) if valid_size(cpu, mem) else None


def _new_state(task_cpu: int, task_mem: int, base: Optional[tuple]) -> Dict[str, Any]:
    """Rung 0 state with the ORIGINAL size: the carried base, else the reclaimed task's size. A
    carried base is never larger than the task that ran (it would raise the limit)."""
    if base and (base[0] > task_cpu or base[1] > task_mem):
        base = None
    cpu, mem = base or (task_cpu, task_mem)
    if not valid_size(int(cpu), int(mem)):
        raise ValueError(f"no valid original size for the ladder: {cpu}x{mem}")
    return {"rung": 0, "base_cpu": int(cpu), "base_mem": int(mem), "reclaims": 0}


def record_reclaim(
    table: Any,
    family: str,
    book: str,
    task_cpu: int,
    task_mem: int,
    rates: Optional[Rates],
    max_fraction: float = DEFAULT_MAX_FRACTION,
    task_id: str = "",
    base: Optional[tuple] = None,
) -> Dict[str, Any]:
    """A Spot task of this family and book was reclaimed: move its next launch up one rung and
    return the new state. The ORIGINAL size is the row's, else ``base`` (the launcher passes it to
    an upsized task, SPOT_LADDER_BASE), else the reclaimed task's own size (it ran at rung 0).
    Idempotent per ``task_id`` (the last RECENT_TASKS are kept): a retried report of a stop does
    not climb twice, even if other reclaims of the book were recorded in between.
    """
    found = get_state(table, family, book)
    if found and task_id and task_id in found.get("recent_tasks", []):
        return found
    state: Dict[str, Any] = found or _new_state(task_cpu, task_mem, base)
    state["rung"] = next_rung(
        state["base_cpu"], state["base_mem"], state["rung"], rates, max_fraction
    )
    state["reclaims"] += 1
    recent = [t for t in state.get("recent_tasks", []) if t != task_id]
    state["recent_tasks"] = (recent + [task_id])[-RECENT_TASKS:] if task_id else recent
    table.put_item(
        Item={
            "cache_key": state_key(family, book),
            "rung": state["rung"],
            "base_cpu": state["base_cpu"],
            "base_mem": state["base_mem"],
            "reclaims": state["reclaims"],
            "recent_tasks": state["recent_tasks"],
            "updated_at": int(time.time()),
            "ttl": int(time.time()) + STATE_TTL_DAYS * 86400,
        }
    )
    return state


def reset(table: Any, family: str, book: str) -> None:
    """The book's task succeeded: its next launch starts at rung 0 again."""
    table.delete_item(Key={"cache_key": state_key(family, book)})


def max_fraction(config: Optional[dict] = None) -> float:
    """``spot_ladder.max_fraction_of_on_demand`` (default 1.0, capped at 1.0: the owner's limit
    may be tightened in config, never loosened)."""
    try:
        value = float(
            ((config or {}).get("spot_ladder") or {}).get(
                "max_fraction_of_on_demand", DEFAULT_MAX_FRACTION
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_FRACTION
    return value if 0 < value <= 1.0 else DEFAULT_MAX_FRACTION
