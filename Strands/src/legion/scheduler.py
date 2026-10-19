"""Dependency-aware parallel scheduler. Deterministic: it, not Ed, owns time, retries and concurrency."""
from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

from .engine import Workflow, WorkflowError
from .runner import Runner
from .settings import Settings
from .tools import Cancelled


@dataclass
class _Running:
    future: Future
    attempt: int
    cancel: threading.Event
    deadline: float
    timed_out: bool = False


@dataclass
class RunSummary:
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    stopped_for_budget: bool = False


class Orchestrator:
    def __init__(self, wf: Workflow, runner: Runner, settings: Settings,
                 emit: Callable[[str], None] = lambda _m: None, poll_s: float = 0.5):
        self.wf, self.runner, self.settings, self.emit, self.poll_s = wf, runner, settings, emit, poll_s

    def run(self, max_tasks: int | None = None) -> RunSummary:
        """Run ready tasks until nothing is runnable (a gate, escalation or budget stop is reached).
        Never exceeds max_parallel_agents concurrent workers; a timed-out worker keeps its slot until it
        actually stops, so the cap is real."""
        wf, s = self.wf, self.settings
        summary = RunSummary(recovered=wf.recover_interrupted())
        for tid in summary.recovered:
            self.emit(f"recovered interrupted task {tid}")
        running: dict[str, _Running] = {}
        launched = 0
        budget_escalated = False
        with ThreadPoolExecutor(max_workers=s.max_parallel_agents, thread_name_prefix="legion") as pool:
            while True:
                if wf.budget_exceeded():
                    summary.stopped_for_budget = True
                    if not budget_escalated:
                        budget_escalated = True
                        wf.escalate("budget", f"Project token budget exhausted ({wf.project()['tokens_used']} of "
                                              f"{s.max_project_tokens}). Raise it or stop.", "engine", blocks_all=True)
                        self.emit("token budget exhausted; escalated and no new tasks will start")
                else:
                    for t in wf.runnable():
                        if len(running) >= s.max_parallel_agents or (max_tasks is not None and launched >= max_tasks):
                            break
                        if t["id"] in running:
                            continue
                        attempt = wf.claim(t["id"], t["agent"])
                        cancel = threading.Event()
                        fut = pool.submit(self._work, t["id"], attempt, cancel)
                        running[t["id"]] = _Running(fut, attempt, cancel, time.monotonic() + s.timeout_for(t["agent"]))
                        launched += 1
                        self.emit(f"start {t['id']} {t['agent']}: {t['title']} (attempt {attempt}, "
                                  f"timeout {s.timeout_for(t['agent'])}s)")
                if not running:
                    return summary
                done, _ = wait([r.future for r in running.values()], timeout=self.poll_s, return_when=FIRST_COMPLETED)
                now = time.monotonic()
                for tid, r in running.items():
                    if not r.timed_out and not r.future.done() and now > r.deadline:
                        r.timed_out = True
                        r.cancel.set()  # Strands stops at the next loop boundary; tools raise Cancelled
                        self.emit(f"timeout {tid}: cancelling")
                for tid in [k for k, r in running.items() if r.future.done()]:
                    self._finish(tid, running.pop(tid), summary)

    def _work(self, task_id: str, attempt: int, cancel: threading.Event) -> None:
        self.runner.execute(task_id, attempt, cancel)

    def _finish(self, tid: str, r: _Running, summary: RunSummary) -> None:
        wf = self.wf
        exc = r.future.exception()
        try:
            if exc is not None and not r.timed_out and wf.task(tid)["status"] == "done":
                # The task finished but follow-up work (e.g. defect triage) blew up: never hide that.
                summary.completed.append(tid)
                msg = f"{tid} completed but post-processing failed: {type(exc).__name__}: {exc}"
                wf.escalate("post_completion_error", msg, "engine", task_id=None)
                self.emit(msg)
                return
            if exc is None and not r.timed_out:
                if wf.task(tid)["status"] == "done":
                    summary.completed.append(tid)
                    self.emit(f"done {tid}")
                    return
                exc = RuntimeError("worker returned without completing the task")
            reason = ("timed out" if r.timed_out or isinstance(exc, Cancelled) else f"{type(exc).__name__}: {exc}")
            wf.fail(tid, r.attempt, reason)
            summary.failed.append(tid)
            self.emit(f"failed {tid}: {reason}")
        except WorkflowError as e:  # attempt superseded (e.g. completed late after recovery): nothing to record
            self.emit(f"ignored stale result for {tid}: {e}")
