"""Batch ROUNDS for a phase run: collect -> submit -> re-run, until a pass queues nothing.

A stage that depends on an earlier answer (an event's entities, an equipment item's refine, its
identity, its source re-check ...) can only be asked once that answer is in, so a run takes several
rounds. Each round is recorded here; past ``cap`` rounds the run stops (no more spend) with an
operator alert and automatic diagnostics:

- requests per round, by kind (cache_type) - which stage is still asking;
- REPEATED requests (asked again in the very next round although that round answered them):
  - answered but NOT in the cache -> the answer was never stored (failed / unmapped result, or an
    invalid answer evicted by the client);
  - answered AND in the cache -> the lookup missed: the key used to look it up differs from the key
    it was submitted under (a volatile value in the prompt or key);
- a repeat is also flagged as soon as it happens (``batch.round_repeats``), before the cap.

Extend ``diagnose`` as new non-convergence cases are found.

The cap is SOFT (owner 2026-11-20): at the cap the run stops, and a human who has read the alert can
allow more rounds (``scripts/approve_batch_rounds.py``, ``round_approval``). Optionally
(``max_attempts``, Phase 3) a request submitted that many times without being answered is SET ASIDE:
it leaves the batch, its step leaves the record unstamped, and one alert names it, so one request
that never gets an answer cannot keep a run going.
"""

import logging
import shlex
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from src.utils.observe import observe, observe_error, observe_warn, publish_alert

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 8
# Phase 2 exit status when a batch round was handed to the poller (EX_TEMPFAIL): not a failure -
# the book resumes when the poller retrieves the batch.
HANDED_OFF_EXIT = 75


def first_round_from_env() -> int:
    """The round a resumed run starts at (``BATCH_ROUND_START``, set by the retrieve task from the
    handed-off batch's name), so the cap counts every round of the book, not per container.
    """
    import os

    try:
        return max(1, int(os.environ.get("BATCH_ROUND_START", "1")))
    except ValueError:
        return 1


def round_of(batch_name: str) -> int:
    """The round in a batch name ("phase2-<book>-<n>files-r<round>-<k>reqs"); 0 if none."""
    import re

    m = re.search(r"-r(\d+)-", batch_name or "")
    return int(m.group(1)) if m else 0


class BatchRoundsExceeded(RuntimeError):
    """The run queued requests past its round cap (alerted): it stops incomplete."""

    def __init__(self, cap: int, scope: str):
        super().__init__(
            f"batch rounds over cap ({cap} submitted, more still queued) for {scope}: "
            "stopped incomplete - see the batch.rounds_over_cap alert"
        )


@dataclass
class Round:
    number: int
    by_kind: Dict[str, int]
    keys: Dict[str, str]  # request_id -> cache_type


@dataclass
class BatchRounds:
    scope: str  # the book(s) of the run, for the alert
    cap: int = DEFAULT_MAX_ROUNDS
    rounds: List[Round] = field(default_factory=list)
    first: int = 1  # a resumed run continues the book's round count
    phase: str = "phase2"  # "phase2" / "phase3": the alert text and the approval row
    max_attempts: int = 0  # 0: never set a request aside
    extra: int = 0  # rounds a human approved past the cap
    asked: Counter = field(default_factory=Counter)  # request_id -> times submitted
    set_aside: Dict[str, str] = field(default_factory=dict)  # request_id -> cache_type

    @property
    def count(self) -> int:
        """The book's current round number (rounds before this run included)."""
        return self.first - 1 + len(self.rounds)

    def admit(self, requests: Iterable[Any], client: Any = None) -> bool:
        """Record the requests queued for the next round. False (and an alert) when that round
        would exceed the cap: the run stops here."""
        keys = {r.request_id: r.cache_type for r in requests}
        rnd = Round(self.count + 1, dict(Counter(keys.values())), keys)
        self.rounds.append(rnd)
        self.asked.update(keys.keys())
        observe(
            "batch",
            "round",
            round=rnd.number,
            requests=len(keys),
            by_kind=_compact(rnd.by_kind),
            books=self.scope,
        )
        repeats = self.repeats()
        if repeats:
            observe_warn(
                "batch",
                "round_repeats",
                round=rnd.number,
                repeated=len(repeats),
                by_kind=_compact(Counter(keys[k] for k in repeats)),
                books=self.scope,
                note="requests answered last round were asked again: see batch_rounds.diagnose",
            )
        if rnd.number <= self.cap + self.extra:
            return True
        from src.utils import round_approval

        self.extra = round_approval.approved_extra(self.phase, self.scope)
        if rnd.number <= self.cap + self.extra:
            return True
        self.alert(client)
        return False

    def drop_stuck(self, collector: Any) -> int:
        """Set aside the queued requests already submitted ``max_attempts`` times without an
        answer (they leave ``collector``); one alert names them. Returns how many were dropped.
        """
        if not self.max_attempts or not collector:
            return 0
        stuck = {
            r.request_id: r.cache_type
            for r in collector.requests
            if self.asked[r.request_id] >= self.max_attempts
        }
        if not stuck:
            return 0
        collector.discard(stuck)
        fresh = {k: v for k, v in stuck.items() if k not in self.set_aside}
        self.set_aside.update(stuck)
        if fresh:
            kinds = Counter(fresh.values())
            example = sorted(fresh)[0]
            observe_error(
                "batch",
                "requests_set_aside",
                requests=len(fresh),
                by_kind=_compact(kinds),
                attempts=self.max_attempts,
                example=example[:40],
                books=self.scope,
                note="submitted without an answer too many times: set aside, their records stay unstamped",
            )
            publish_alert(
                f"WWII Pipeline: {self._label()} batch requests set aside ({self.scope})"[
                    :100
                ],
                f"{len(fresh)} request(s) were submitted {self.max_attempts} times without an "
                f"answer and were set aside: {_compact(kinds)} (example key {example[:40]}). "
                "Their records are NOT stamped, so the next run asks again. Look at the "
                "batch.round_repeats / Batch result failed lines of this task: a request that "
                "never gets a stored answer is a failed or unmapped result, or a prompt that "
                "changes between rounds.",
            )
        return len(stuck)

    def _label(self) -> str:
        return "Phase 3" if self.phase == "phase3" else "Phase 2"

    def complete(self, seconds: float) -> None:
        """The book's rounds are done (a pass queued nothing): one summary line - the book's
        round count (rounds of earlier tasks included), this task's rounds, requests and time.
        """
        observe(
            "batch",
            "rounds_complete",
            books=self.scope,
            rounds=self.count,
            this_task_rounds=len(self.rounds),
            this_task_requests=sum(sum(r.by_kind.values()) for r in self.rounds),
            this_task_by_kind=_compact(
                sum((Counter(r.by_kind) for r in self.rounds), Counter())
            ),
            this_task_minutes=round(seconds / 60, 1),
            resumed=self.first > 1,
        )

    def repeats(self) -> Set[str]:
        """Keys queued this round that the previous round already submitted (and answered)."""
        if len(self.rounds) < 2:
            return set()
        return set(self.rounds[-1].keys) & set(self.rounds[-2].keys)

    def diagnose(self, client: Any = None) -> Dict[str, Any]:
        repeats = self.repeats()
        last = self.rounds[-1].keys if self.rounds else {}
        stored: Counter = Counter()
        missing: Counter = Counter()
        unknown: Counter = Counter()
        book = "" if "," in self.scope else self.scope
        for key in repeats:
            kind = last[key]
            found = _cached(client, kind, key, book)
            bucket = unknown if found is None else (stored if found else missing)
            bucket[kind] += 1
        return {
            "rounds": [
                (r.number, sum(r.by_kind.values()), r.by_kind) for r in self.rounds
            ],
            "repeated": len(repeats),
            "repeated_answer_not_stored": dict(missing),
            "repeated_lookup_key_differs": dict(stored),
            "repeated_cache_unreadable": dict(unknown),
            "still_asking": sorted(self.rounds[-1].by_kind) if self.rounds else [],
        }

    def alert(self, client: Any = None) -> None:
        d = self.diagnose(client)
        observe_error(
            "batch",
            "rounds_over_cap",
            rounds=self.count,
            cap=self.cap + self.extra,
            books=self.scope,
            repeated=d["repeated"],
            still_asking=",".join(d["still_asking"]),
            note="batch rounds did not converge: the run stopped without submitting more",
        )
        publish_alert(
            f"WWII Pipeline: {self._label()} batch rounds over cap ({self.scope})"[
                :100
            ],
            _alert_text(self, d),
        )


def _cached(client: Any, kind: str, key: str, book: str) -> Optional[bool]:
    """Is the answer in the cache? None when it cannot be told (diagnostics never raise). The
    cache of a book-bound kind is the BOOK's: looked up with the run's book in scope."""
    try:
        from src.grok_client import book_scope

        with book_scope(book or None):
            return key in client._get_cache(kind)  # pylint: disable=protected-access
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return None


def _compact(by_kind: Dict[str, int]) -> str:
    return ",".join(f"{k}:{v}" for k, v in sorted(by_kind.items()))


def _alert_text(rounds: BatchRounds, d: Dict[str, Any]) -> str:
    label = rounds._label()  # pylint: disable=protected-access
    lines = [
        f"{label} for {rounds.scope} submitted {rounds.count - 1} batch rounds (cap "
        f"{rounds.cap + rounds.extra}) and still had requests queued for round {rounds.count}, "
        "which was NOT sent. The phase failed: the book is incomplete"
        + (
            " (no dedup / Phase 3 on it) and is picked up again on its next Phase 2 run."
            if rounds.phase == "phase2"
            else " and is picked up again on its next Phase 3 run."
        ),
        "",
        "If the rounds are making progress (each asks NEW requests), allow more and re-run:",
        f"  python3 scripts/approve_batch_rounds.py --phase {rounds.phase} "
        f"--scope {shlex.quote(rounds.scope)} --extra 4",
        "",
        "Requests per round (round: total - by kind; the last was not sent):",
    ]
    lines += [f"  {n}: {total} - {_compact(kinds)}" for n, total, kinds in d["rounds"]]
    lines += [
        "",
        f"Still asking: {', '.join(d['still_asking']) or '-'}",
        f"Asked again after being answered last round: {d['repeated']}",
    ]
    if d["repeated_answer_not_stored"]:
        lines.append(
            "  answer never stored (failed / unmapped batch result, or an invalid answer the "
            f"client evicted): {_compact(d['repeated_answer_not_stored'])}"
        )
    if d["repeated_lookup_key_differs"]:
        lines.append(
            "  answer stored but looked up under a different key (a volatile value in the prompt "
            f"or cache key): {_compact(d['repeated_lookup_key_differs'])}"
        )
    if d["repeated_cache_unreadable"]:
        lines.append(
            "  cache could not be read to tell which: "
            f"{_compact(d['repeated_cache_unreadable'])}"
        )
    if not d["repeated"]:
        lines.append(
            "  none repeated: each round asks NEW requests - a stage that keeps finding new work "
            "(e.g. a chain of dependent calls per item) or a cap set too low for this book"
        )
    lines += ["", "Logs: OBSERV batch.round / batch.round_repeats lines of this task."]
    return "\n".join(lines)
