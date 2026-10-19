"""Deterministic workflow engine. Personality is behavioural; this module is the enforcement.

Agents never touch records directly: they go through these methods (via tools.py), so the rules
below hold regardless of what any model says.
"""
from __future__ import annotations

import logging
from typing import Callable

from .roster import ROSTER
from .settings import Settings
from .store import Record, Store, now, slugify

log = logging.getLogger(__name__)

GATES = ("project_approval", "technical_design_approval", "preproduction_approval",
         "production_deployment_approval")
# stage -> number of approved gates required before tasks in it may run
STAGES = {"discovery": 0, "design": 1, "implementation": 2, "validation": 2, "release": 3, "production": 4}
SEVERITIES = ("critical", "major", "minor")
BLOCKING = ("critical", "major")
MIN_EVIDENCE_CHARS = 40
ACTIVE = ("blocked", "ready", "in_progress")


class WorkflowError(ValueError):
    """A rule was violated. Messages are written to be read by agents and humans."""


class Workflow:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.listeners: list[Callable[[str, Record], None]] = []

    # -- helpers ---------------------------------------------------------------------------------
    def _emit(self, kind: str, rec: Record) -> None:
        for fn in self.listeners:
            try:
                fn(kind, rec)
            except Exception as exc:  # integrations must never break workflow enforcement
                self.store.audit("engine", f"listener failed on {kind} {rec.get('id')}", repr(exc))

    def project(self) -> Record:
        if not self.store.exists("project"):
            raise WorkflowError("No project in this state directory.")
        return self.store.read("project")

    def gate_index(self) -> int:
        return int(self.project()["gate_index"])

    def task(self, task_id: str) -> Record:
        if not self.store.exists("tasks", task_id):
            raise WorkflowError(f"Unknown task: {task_id}")
        return self.store.read("tasks", task_id)

    def _save(self, kind: str, rec: Record) -> None:
        self.store.write(kind, rec.get("id") if kind != "project" else None, rec)
        self._emit(kind, rec)

    def tasks(self) -> list[Record]:
        return self.store.list("tasks")

    # -- project & gates ---------------------------------------------------------------------------
    def create_project(self, name: str, objective: str, educational: bool = False) -> str:
        if not name.strip() or not objective.strip():
            raise WorkflowError("Project name and objective are required.")
        if self.store.exists("project"):
            raise WorkflowError("A project already exists in this state directory.")
        with self.store.lock():
            rec = Record({"name": name, "slug": slugify(name), "status": "awaiting_project_approval",
                          "gate_index": 0, "educational": educational, "merge_count": 0,
                          "tokens_used": 0, "created": now()}, f"# {name}\n\n{objective.strip()}\n")
            self._save("project", rec)
            self.store.audit("human", "created project", name)
        return rec["slug"]

    def objective(self) -> str:
        return self.project().body

    def approve_gate(self, gate: str, approver: str, evidence: str) -> None:
        with self.store.lock():
            proj = self.project()
            idx = proj["gate_index"]
            if idx >= len(GATES) or gate != GATES[idx]:
                raise WorkflowError(f"Gate order enforced; expected {GATES[idx] if idx < len(GATES) else 'none'}.")
            if not approver.strip() or not evidence.strip():
                raise WorkflowError("Approval requires a named approver and recorded evidence/decision.")
            self._check_gate_ready(gate, proj)
            rec = Record({"id": f"{idx + 1:02d}-{gate}", "gate": gate, "approver": approver,
                          "approved_at": now()}, evidence.strip() + "\n")
            self.store.write("approvals", rec["id"], rec)
            proj["gate_index"] = idx + 1
            proj["status"] = ("planning", "implementing", "release_preparation", "deployment_approved")[idx]
            self.store.log(proj, f"gate approved: {gate} by {approver}")
            self._save("project", proj)
            if gate == "technical_design_approval":
                self.freeze_baseline()
            self.refresh()
            self.store.audit(approver, "approved gate", gate)

    def _check_gate_ready(self, gate: str, proj: Record) -> None:
        open_esc = [e["id"] for e in self.store.list("escalations") if e["status"] == "open"]
        if open_esc:
            raise WorkflowError(f"Resolve open escalations first: {', '.join(open_esc)}.")
        tasks = self.tasks()

        def pending(stages: tuple[str, ...]) -> list[str]:
            return [t["id"] for t in tasks if t["stage"] in stages and t["status"] != "done"
                    and t["status"] != "cancelled"]

        required = {"project_approval": ("discovery",), "technical_design_approval": ("design",),
                    "preproduction_approval": ("implementation", "validation"),
                    "production_deployment_approval": ("release",)}[gate]
        if left := pending(required):
            raise WorkflowError(f"Tasks not complete for this gate: {', '.join(left)}.")
        if gate == "technical_design_approval" and not any(t["stage"] == "implementation" for t in tasks):
            raise WorkflowError("Plan implementation tasks before design approval so the human can see scope.")
        if gate == "preproduction_approval":
            if [p["id"] for p in self.store.list("prs") if p["status"] in ("open", "approved", "escalated")]:
                raise WorkflowError("Every pull request must be merged or closed before pre-production approval.")
            val = self.latest_validation()
            if not val or not val["passed"]:
                raise WorkflowError("Pre-production approval requires Eleanor's passing validation.")
            if val["merge_count"] != proj["merge_count"]:
                raise WorkflowError("Code changed after Eleanor's validation; re-validate.")

    # -- tasks -----------------------------------------------------------------------------------------
    def add_task(self, title: str, agent: str, stage: str, *, description: str = "", kind: str = "non_code",
                 depends_on: list[str] | None = None, acceptance: list[str] | None = None,
                 created_by: str = "human", task_id: str | None = None) -> str:
        depends_on = list(depends_on or [])
        with self.store.lock():
            proj = self.project()
            if stage not in STAGES:
                raise WorkflowError(f"Unknown stage '{stage}'. Use: {', '.join(STAGES)}.")
            if agent not in ROSTER:
                raise WorkflowError(f"Unknown agent: {agent}")
            if not title.strip():
                raise WorkflowError("Task title is required.")
            if proj["gate_index"] >= 3 and stage not in ("release", "production"):
                raise WorkflowError("Scope is frozen after pre-production approval.")
            if proj["gate_index"] > STAGES[stage] and stage not in ("release", "production"):
                raise WorkflowError(f"Stage '{stage}' is already closed.")
            if agent == "will" and not proj["educational"]:
                raise WorkflowError("Will is only activated for projects with an educational component.")
            if kind not in ("code", "non_code"):
                raise WorkflowError("kind must be code or non_code.")
            if kind == "code" and not (ROSTER[agent].writes_code and stage in ("implementation", "release")):
                raise WorkflowError(f"{agent} cannot own code tasks in stage '{stage}'.")
            if stage == "validation" and agent != "eleanor":
                raise WorkflowError("Validation tasks belong to Eleanor.")
            if agent == "eleanor" and stage != "validation":
                raise WorkflowError("Eleanor only works in the validation stage.")
            if agent == "michael":
                raise WorkflowError("Michael reviews pull requests; he is not assigned tasks.")
            if stage == "implementation" and kind == "code" and not acceptance:
                raise WorkflowError("Code tasks need acceptance criteria.")
            known = {t["id"] for t in self.tasks()}
            if missing := set(depends_on) - known:
                raise WorkflowError(f"Unknown dependencies: {', '.join(sorted(missing))}")
            tid = task_id or self.store.next_id("tasks", "T")
            if self.store.exists("tasks", tid):
                raise WorkflowError(f"Duplicate task id: {tid}")
            rec = Record({"id": tid, "title": title.strip(), "agent": agent, "stage": stage, "kind": kind,
                          "status": "blocked", "depends_on": depends_on, "acceptance": acceptance or [],
                          "description": description, "attempts": 0, "tokens_used": 0, "created_by": created_by,
                          "created": now(), "jira_key": None, "branch": None, "pr": None})
            self.store.log(rec, f"created by {created_by}")
            self._save("tasks", rec)
            self.refresh()
            self.store.audit(created_by, "created task", f"{tid} -> {agent} [{stage}] {title}")
            return tid

    def cancel_task(self, task_id: str, by: str, reason: str) -> None:
        with self.store.lock():
            t = self.task(task_id)
            if t["status"] in ("done", "in_progress"):
                raise WorkflowError(f"Cannot cancel a {t['status']} task.")
            dependents = [x["id"] for x in self.tasks() if task_id in x["depends_on"] and x["status"] != "cancelled"]
            if dependents:
                raise WorkflowError(f"Cancel dependents first: {', '.join(dependents)}.")
            t["status"] = "cancelled"
            self.store.log(t, f"cancelled by {by}: {reason}")
            self._save("tasks", t)

    def _deps_done(self, t: Record, by_id: dict[str, Record]) -> bool:
        return all(by_id[d]["status"] == "done" for d in t["depends_on"])

    def refresh(self) -> None:
        with self.store.lock():
            gates = self.gate_index()
            by_id = {t["id"]: t for t in self.tasks()}
            for t in by_id.values():
                if t["status"] in ("blocked", "ready"):
                    want = "ready" if self._deps_done(t, by_id) and STAGES[t["stage"]] <= gates else "blocked"
                    if want != t["status"]:
                        t["status"] = want
                        self._save("tasks", t)

    def runnable(self) -> list[Record]:
        """Ready tasks, minus those held by an open escalation. Oldest first."""
        with self.store.lock():
            esc = [e for e in self.store.list("escalations") if e["status"] == "open"]
            if any(e.get("blocks_all") for e in esc):
                return []
            held = {e.get("task_id") for e in esc}
            return [t for t in self.tasks() if t["status"] == "ready" and t["id"] not in held]

    def claim(self, task_id: str, agent: str) -> int:
        with self.store.lock():
            t = self.task(task_id)
            if t["agent"] != agent:
                raise WorkflowError(f"{task_id} is assigned to {t['agent']}, not {agent}.")
            if t["id"] not in {r["id"] for r in self.runnable()}:
                raise WorkflowError(f"{task_id} is not runnable (status {t['status']}).")
            t["status"], t["attempts"], t["started"] = "in_progress", t["attempts"] + 1, now()
            self.store.log(t, f"claimed by {agent} (attempt {t['attempts']})")
            self._save("tasks", t)
            return t["attempts"]

    def _current(self, task_id: str, attempt: int) -> Record:
        t = self.task(task_id)
        if t["status"] != "in_progress" or t["attempts"] != attempt:
            raise WorkflowError(f"{task_id} attempt {attempt} is no longer the active attempt.")
        return t

    def complete(self, task_id: str, attempt: int, summary: str, deliverable: str | None = None) -> None:
        with self.store.lock():
            t = self._current(task_id, attempt)
            if t["kind"] == "code":
                pr = self.store.read("prs", t["pr"]) if t["pr"] else None
                if not pr or pr["status"] != "merged":
                    raise WorkflowError("A code task completes only after its PR is reviewed and merged.")
            elif not deliverable:
                raise WorkflowError("A non-code task needs a recorded deliverable.")
            t["status"], t["deliverable"], t["finished"] = "done", deliverable, now()
            self.store.log(t, f"done: {summary[:300]}")
            self._save("tasks", t)
            self.refresh()
            self.store.audit(t["agent"], "completed task", task_id)

    def fail(self, task_id: str, attempt: int, reason: str) -> None:
        with self.store.lock():
            t = self._current(task_id, attempt)
            self.store.log(t, f"attempt {attempt} failed: {reason[:300]}")
            if t["attempts"] >= self.settings.max_task_attempts:
                t["status"] = "failed"
                self._save("tasks", t)
                self.escalate("task_failed", f"{task_id} failed {t['attempts']} times: {reason[:300]}",
                              raised_by="engine", task_id=task_id)
            else:
                t["status"] = "ready"
                self._save("tasks", t)
            self.store.audit("engine", "task attempt failed", f"{task_id}: {reason[:200]}")

    def retry(self, task_id: str, by: str, reason: str) -> None:
        """Human/Ed-driven reset of a failed task, e.g. after resolving its escalation."""
        with self.store.lock():
            t = self.task(task_id)
            if t["status"] not in ("failed", "in_progress"):
                raise WorkflowError("Only failed or interrupted tasks can be retried.")
            t["status"], t["attempts"] = "ready", 0
            self.store.log(t, f"reset by {by}: {reason}")
            self._save("tasks", t)

    def recover_interrupted(self) -> list[str]:
        """Resume after a crash: tasks left in_progress by a dead worker go back into the queue."""
        out = []
        with self.store.lock():
            for t in self.tasks():
                if t["status"] == "in_progress":
                    self.fail(t["id"], t["attempts"], "worker interrupted before completion")
                    out.append(t["id"])
        return out

    def overdue(self) -> list[str]:
        """In-progress tasks past their agent's timeout; the orchestrator, not Ed, tracks time."""
        from datetime import datetime
        out = []
        for t in self.tasks():
            if t["status"] == "in_progress" and t.get("started"):
                age = (datetime.fromisoformat(now()) - datetime.fromisoformat(t["started"])).total_seconds()
                if age > self.settings.timeout_for(t["agent"]):
                    out.append(t["id"])
        return out

    # -- decisions, escalations, ideas ------------------------------------------------------------------
    def record_decision(self, author: str, text: str, task_id: str | None = None, baseline: bool = False) -> str:
        with self.store.lock():
            if not text.strip():
                raise WorkflowError("A decision needs text.")
            did = self.store.next_id("decisions", "D")
            rec = Record({"id": did, "author": author, "task": task_id, "baseline": baseline,
                          "created": now()}, text.strip() + "\n")
            self._save("decisions", rec)
            if task_id and self.store.exists("tasks", task_id):
                t = self.task(task_id)
                self.store.log(t, f"decision {did} by {author}: {text.strip()[:200]}")
                self._save("tasks", t)
            self.store.audit(author, "recorded decision", did)
            return did

    def freeze_baseline(self) -> None:
        """Called at design approval: design-stage decisions become the architecture baseline."""
        with self.store.lock():
            for d in self.store.list("decisions"):
                t = self.store.read("tasks", d["task"]) if d.get("task") and self.store.exists("tasks", d["task"]) else None
                if t and t["stage"] == "design" and not d["baseline"]:
                    d["baseline"] = True
                    self.store.write("decisions", d["id"], d)

    def escalate(self, kind: str, summary: str, raised_by: str, task_id: str | None = None,
                 blocks_all: bool = False) -> str:
        with self.store.lock():
            eid = self.store.next_id("escalations", "E")
            rec = Record({"id": eid, "kind": kind, "raised_by": raised_by, "task_id": task_id,
                          "status": "open", "blocks_all": blocks_all, "created": now()}, summary.strip() + "\n")
            self.store.write("escalations", eid, rec)
            self.store.audit(raised_by, "escalated", f"{eid} {kind}: {summary[:160]}")
            return eid

    def resolve_escalation(self, eid: str, resolver: str, resolution: str) -> None:
        with self.store.lock():
            e = self.store.read("escalations", eid)
            if e["status"] != "open":
                raise WorkflowError(f"{eid} is already resolved.")
            if not resolution.strip():
                raise WorkflowError("A resolution is required; silence is not approval.")
            e["status"], e["resolved_by"], e["resolved_at"] = "resolved", resolver, now()
            e.body += f"\n## Resolution\n{resolution.strip()}\n"
            self.store.write("escalations", eid, e)
            self.store.audit(resolver, "resolved escalation", eid)

    def challenge_decision(self, decision_id: str, raised_by: str, evidence: str) -> str:
        """Reopening a baseline decision needs substantial new evidence and goes to the human."""
        d = self.store.read("decisions", decision_id)
        if not d["baseline"]:
            raise WorkflowError("Only baseline architecture decisions need escalation to change.")
        if len(evidence.strip()) < MIN_EVIDENCE_CHARS:
            raise WorkflowError("Insufficient new evidence to reopen an approved decision.")
        if any(e["status"] == "open" and e["kind"] == "architecture_change" and decision_id in e.body
               for e in self.store.list("escalations")):
            raise WorkflowError(f"{decision_id} is already under challenge.")
        return self.escalate("architecture_change", f"Challenge to {decision_id} by {raised_by}: {evidence}",
                             raised_by, task_id=d.get("task"))

    def add_idea(self, author: str, title: str, text: str) -> str:
        with self.store.lock():
            iid = self.store.next_id("backlog", "I")
            self.store.write("backlog", iid, Record({"id": iid, "author": author, "title": title,
                                                      "created": now()}, text.strip() + "\n"))
            return iid

    # -- pull requests ----------------------------------------------------------------------------------
    def open_pr(self, task_id: str, branch: str, title: str, author: str, number: int | None = None,
                url: str | None = None) -> str:
        with self.store.lock():
            t = self.task(task_id)
            if t["kind"] != "code":
                raise WorkflowError("Only code tasks open pull requests.")
            if t["pr"] and self.store.read("prs", t["pr"])["status"] != "closed":
                raise WorkflowError(f"{task_id} already has pull request {t['pr']}.")
            pid = self.store.next_id("prs", "PR")
            rec = Record({"id": pid, "task": task_id, "branch": branch, "title": title, "author": author,
                          "status": "open", "round": 0, "number": number, "url": url, "findings": [],
                          "created": now()})
            self.store.log(rec, f"opened by {author}")
            self.store.write("prs", pid, rec)
            t["pr"], t["branch"] = pid, branch
            self._save("tasks", t)
            return pid

    def review_pr(self, pr_id: str, reviewer: str, verdict: str, summary: str,
                  findings: list[dict] | None = None) -> str:
        """Record Michael's review. Returns the effective verdict (the engine may downgrade an approval)."""
        findings = findings or []
        with self.store.lock():
            pr = self.store.read("prs", pr_id)
            if reviewer != "michael":
                raise WorkflowError("Only Michael may review pull requests.")
            if pr["author"] == reviewer:
                raise WorkflowError("Authors cannot review their own pull requests.")
            if pr["status"] not in ("open", "approved"):
                raise WorkflowError(f"{pr_id} is {pr['status']}; it cannot be reviewed.")
            if verdict not in ("approved", "changes_requested"):
                raise WorkflowError("Verdict must be approved or changes_requested.")
            pr["round"] += 1
            for f in findings:
                if f.get("severity") not in SEVERITIES:
                    raise WorkflowError(f"Finding severity must be one of {SEVERITIES}.")
                pr["findings"].append({"id": len(pr["findings"]) + 1, "round": pr["round"],
                                       "severity": f["severity"], "text": f["text"], "resolved": False,
                                       "resolution": None})
            unresolved = [f for f in pr["findings"] if f["severity"] in BLOCKING and not f["resolved"]]
            effective = "approved" if verdict == "approved" and not unresolved else "changes_requested"
            if verdict == "approved" and unresolved:
                self.store.log(pr, "approval downgraded: unresolved critical/major findings remain")
            pr["status"] = "approved" if effective == "approved" else "open"
            self.store.log(pr, f"round {pr['round']} by {reviewer}: {effective}. {summary[:300]}")
            if effective != "approved" and pr["round"] >= self.settings.max_review_rounds:
                pr["status"] = "escalated"
                self.escalate("review_deadlock", f"{pr_id} not approved after {pr['round']} review rounds.",
                              "engine", task_id=pr["task"])
            self.store.write("prs", pr_id, pr)
            self.store.audit(reviewer, f"reviewed {pr_id}", effective)
            return effective

    def resolve_finding(self, pr_id: str, finding_id: int, resolver: str, resolution: str) -> None:
        with self.store.lock():
            pr = self.store.read("prs", pr_id)
            if resolver != pr["author"]:
                raise WorkflowError("Only the PR author resolves findings (Michael then re-reviews).")
            if not resolution.strip():
                raise WorkflowError("Explain how the finding was addressed or why it is not applicable.")
            for f in pr["findings"]:
                if f["id"] == finding_id:
                    f["resolved"], f["resolution"] = True, resolution.strip()
                    self.store.write("prs", pr_id, pr)
                    return
            raise WorkflowError(f"{pr_id} has no finding {finding_id}.")

    def authorize_merge(self, pr_id: str, checks_passed: bool) -> None:
        pr = self.store.read("prs", pr_id)
        if pr["status"] != "approved":
            raise WorkflowError(f"{pr_id} has no Michael approval (status: {pr['status']}).")
        if not checks_passed:
            raise WorkflowError("Automated checks must pass before merge.")

    def mark_merged(self, pr_id: str) -> None:
        with self.store.lock():
            self.authorize_merge(pr_id, True)
            pr = self.store.read("prs", pr_id)
            pr["status"] = "merged"
            self.store.log(pr, "merged")
            self.store.write("prs", pr_id, pr)
            proj = self.project()
            proj["merge_count"] += 1
            self._save("project", proj)
            self.store.audit("engine", "merged", pr_id)

    # -- validation -------------------------------------------------------------------------------------
    def record_validation(self, tester: str, passed: bool, evidence: str, defects: list[dict] | None = None) -> str:
        defects = defects or []
        with self.store.lock():
            if tester != "eleanor":
                raise WorkflowError("Only Eleanor records validation results.")
            if not evidence.strip():
                raise WorkflowError("Validation requires evidence.")
            if any(d.get("severity") == "critical" for d in defects):
                passed = False  # Eleanor can block release; criticals always do
            vid = self.store.next_id("validations", "V")
            rec = Record({"id": vid, "tester": tester, "passed": passed, "defects": defects,
                          "merge_count": self.project()["merge_count"], "created": now()}, evidence.strip() + "\n")
            self.store.write("validations", vid, rec)
            self.store.audit(tester, "validation", f"{vid} passed={passed}")
            return vid

    def latest_validation(self) -> Record | None:
        vals = self.store.list("validations")
        return vals[-1] if vals else None

    # -- budget & status ---------------------------------------------------------------------------------
    def record_usage(self, task_id: str, tokens: int) -> None:
        with self.store.lock():
            if task_id:
                t = self.task(task_id)
                t["tokens_used"] += tokens
                self._save("tasks", t)
            proj = self.project()
            proj["tokens_used"] += tokens
            self.store.write("project", None, proj)

    def budget_exceeded(self) -> bool:
        return self.project()["tokens_used"] >= self.settings.max_project_tokens

    def status(self) -> dict:
        proj = self.project()
        tasks = self.tasks()
        return {"project": proj["name"], "status": proj["status"], "gates_approved": proj["gate_index"],
                "next_gate": GATES[proj["gate_index"]] if proj["gate_index"] < len(GATES) else None,
                "tokens_used": proj["tokens_used"], "tasks": {s: sum(t["status"] == s for t in tasks)
                                                              for s in ("blocked", "ready", "in_progress", "done", "failed", "cancelled")},
                "open_escalations": [e["id"] for e in self.store.list("escalations") if e["status"] == "open"],
                "open_prs": [p["id"] for p in self.store.list("prs") if p["status"] not in ("merged", "closed")],
                "overdue": self.overdue()}
