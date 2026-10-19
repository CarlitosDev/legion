#!/usr/bin/env python3
"""Small, framework-neutral workflow kernel for the autonomous workforce."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
from typing import Any

GATES = (
    "project_approval",
    "technical_design_approval",
    "preproduction_approval",
    "production_deployment_approval",
)
AGENT_IDS = {
    "tim", "ed", "ralph", "carlos", "teddy", "david", "will",
    "eleanor", "hadrian", "renato", "demis", "radek", "michael",
}


@contextmanager
def state_lock(path: Path):
    """Serialize local read/modify/write cycles; release automatically on process exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def load(path: Path) -> dict[str, Any]:
    state = json.loads(path.read_text()) if path.exists() else {
        "project": None, "gate_index": 0, "approvals": [], "tasks": [],
    }
    state.setdefault("pull_requests", [])
    state.setdefault("validation", None)
    state.setdefault("validation_history", [])
    for task in state["tasks"]:
        task.setdefault("kind", "code")
        task.setdefault("claim_history", [])
        task.setdefault("retry_history", [])
    for pr in state["pull_requests"]:
        pr.setdefault("attempt", 0)
    return state


def save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def create_project(state: dict[str, Any], name: str, objective: str) -> None:
    if not name.strip() or not objective.strip():
        raise ValueError("Project name and objective are required.")
    if state["project"]:
        raise ValueError("A project already exists in this state file.")
    state["project"] = {"name": name, "objective": objective, "status": "awaiting_approval"}


def add_task(state: dict[str, Any], task_id: str, title: str, agent: str,
             depends_on: list[str], kind: str = "code") -> None:
    if not state["project"]:
        raise ValueError("Create a project before adding tasks.")
    if state["gate_index"] >= 3:
        raise ValueError("Project scope is frozen after pre-production approval.")
    if agent not in AGENT_IDS:
        raise ValueError(f"Unknown agent: {agent}")
    if kind not in {"code", "non_code"}:
        raise ValueError("Task kind must be code or non_code.")
    if not task_id.strip() or not title.strip():
        raise ValueError("Task id and title are required.")
    if any(t["id"] == task_id for t in state["tasks"]):
        raise ValueError(f"Duplicate task id: {task_id}")
    known = {t["id"] for t in state["tasks"]}
    missing = set(depends_on) - known
    if missing:
        raise ValueError(f"Unknown dependencies: {', '.join(sorted(missing))}")
    state["tasks"].append({
        "id": task_id, "title": title, "agent": agent,
        "depends_on": depends_on, "kind": kind,
        "status": "ready" if state["gate_index"] >= 2 and not depends_on else "blocked",
    })
    state["validation"] = None


def approve_gate(state: dict[str, Any], gate: str, approver: str, evidence: str) -> None:
    if not state["project"]:
        raise ValueError("Create a project before requesting approvals.")
    expected_index = state["gate_index"]
    if expected_index >= len(GATES) or gate != GATES[expected_index]:
        expected = GATES[expected_index] if expected_index < len(GATES) else "all gates complete"
        raise ValueError(f"Gate order enforced; expected {expected}.")
    if not approver.strip():
        raise ValueError("Approval requires a recorded approver.")
    if not evidence.strip():
        raise ValueError("Approval requires a recorded decision or evidence.")
    if gate == "preproduction_approval":
        if not state["tasks"] or any(t["status"] != "done" for t in state["tasks"]):
            raise ValueError("Pre-production approval requires planned tasks and every task to be complete.")
        if any(pr["status"] == "open" for pr in state["pull_requests"]):
            raise ValueError("Pre-production approval requires every pull request to be closed or merged.")
        if not state["validation"] or not state["validation"]["passed"]:
            raise ValueError("Pre-production approval requires Eleanor's passing QA validation.")
    state["approvals"].append({
        "gate": gate, "approver": approver, "evidence": evidence.strip()
    })
    state["gate_index"] += 1
    if state["gate_index"] == 1:
        state["project"]["status"] = "planning"
    if state["gate_index"] == 2:
        state["project"]["status"] = "implementing"
        refresh_ready_tasks(state)
    if state["gate_index"] == 3:
        state["project"]["status"] = "preproduction_approved"
    if state["gate_index"] == len(GATES) and state["project"]:
        state["project"]["status"] = "deployment_approved"


def ready_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    if state["gate_index"] < 2:
        return []
    completed = {t["id"] for t in state["tasks"] if t["status"] == "done"}
    return [t for t in state["tasks"] if t["status"] in ("ready", "blocked")
            and set(t["depends_on"]) <= completed]


def refresh_ready_tasks(state: dict[str, Any]) -> None:
    completed = {t["id"] for t in state["tasks"] if t["status"] == "done"}
    for task in state["tasks"]:
        if task["status"] == "blocked" and state["gate_index"] >= 2:
            if set(task["depends_on"]) <= completed:
                task["status"] = "ready"


def mark_done(state: dict[str, Any], task_id: str) -> None:
    task = next((t for t in state["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise ValueError(f"Unknown task: {task_id}")
    if task["status"] != "in_progress":
        raise ValueError(f"Task {task_id} must be claimed and in progress before completion.")
    current_attempt = task["claim_history"][-1]["attempt"]
    if task["kind"] == "code" and not any(
        pr["task_id"] == task_id and pr["attempt"] == current_attempt and pr["status"] == "merged"
        for pr in state["pull_requests"]
    ):
        raise ValueError(f"Code task {task_id} requires a merged PR reviewed by Michael for the current attempt.")
    task["status"] = "done"
    completed = {t["id"] for t in state["tasks"] if t["status"] == "done"}
    for item in state["tasks"]:
        if (item["status"] == "blocked" and state["gate_index"] >= 2
                and set(item["depends_on"]) <= completed):
            item["status"] = "ready"


def claim_task(state: dict[str, Any], task_id: str, agent: str) -> None:
    task = next((t for t in state["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise ValueError(f"Unknown task: {task_id}")
    if agent != task["agent"]:
        raise ValueError(f"Task {task_id} is assigned agent {task['agent']}.")
    if task not in ready_tasks(state):
        raise ValueError(f"Task {task_id} is not ready to be claimed.")
    task["status"] = "in_progress"
    task.setdefault("claim_history", []).append({"agent": agent, "attempt": len(task.get("claim_history", [])) + 1})


def retry_task(state: dict[str, Any], task_id: str, reason: str,
               agent: str | None = None) -> None:
    task = next((t for t in state["tasks"] if t["id"] == task_id), None)
    if task is None or task["status"] != "in_progress":
        raise ValueError(f"Task {task_id} is not in progress and cannot be retried.")
    if not reason.strip():
        raise ValueError("Retry requires a recorded reason.")
    if any(pr["task_id"] == task_id and pr["status"] == "open" for pr in state["pull_requests"]):
        raise ValueError(f"Close the open pull request for task {task_id} before retrying it.")
    next_agent = agent or task["agent"]
    if next_agent not in AGENT_IDS:
        raise ValueError(f"Unknown agent: {next_agent}")
    task.setdefault("retry_history", []).append({
        "reason": reason.strip(), "previous_agent": task["agent"], "next_agent": next_agent,
    })
    task["agent"] = next_agent
    task["status"] = "ready"


def open_pull_request(state: dict[str, Any], pr_id: str, title: str, task_id: str) -> None:
    if state["gate_index"] != 2:
        if state["gate_index"] >= 3:
            raise ValueError("Project scope is frozen after pre-production approval.")
        raise ValueError("Pull requests can be opened after technical design approval.")
    if any(pr["id"] == pr_id for pr in state["pull_requests"]):
        raise ValueError(f"Duplicate pull request id: {pr_id}")
    task = next((task for task in state["tasks"] if task["id"] == task_id), None)
    if task is None:
        raise ValueError(f"Unknown task for pull request: {task_id}")
    if task["kind"] != "code":
        raise ValueError("Pull requests can only be linked to code tasks.")
    if task["status"] != "in_progress":
        raise ValueError(f"Code task {task_id} must be claimed before opening a pull request.")
    if not pr_id.strip() or not title.strip():
        raise ValueError("Pull request id and title are required.")
    attempt = task["claim_history"][-1]["attempt"]
    state["pull_requests"].append({
        "id": pr_id, "title": title, "task_id": task_id, "attempt": attempt, "status": "open",
        "reviews": [],
    })
    state["validation"] = None


def close_pull_request(state: dict[str, Any], pr_id: str, reason: str) -> None:
    if state["gate_index"] >= 3:
        raise ValueError("Project scope is frozen after pre-production approval.")
    if not reason.strip():
        raise ValueError("Closing a pull request requires a reason.")
    pr = next((item for item in state["pull_requests"] if item["id"] == pr_id), None)
    if pr is None or pr["status"] != "open":
        raise ValueError(f"Unknown or closed pull request: {pr_id}")
    pr["status"] = "closed"
    pr["closure_reason"] = reason.strip()
    state["validation"] = None


def review_pull_request(state: dict[str, Any], pr_id: str, reviewer: str,
                        decision: str, evidence: str) -> None:
    if state["gate_index"] >= 3:
        raise ValueError("Project scope is frozen after pre-production approval.")
    if reviewer != "michael":
        raise ValueError("Michael must review every pull request.")
    if decision not in {"approved", "changes_requested"}:
        raise ValueError("Review decision must be approved or changes_requested.")
    if not evidence.strip():
        raise ValueError("A review requires evidence.")
    pr = next((item for item in state["pull_requests"] if item["id"] == pr_id), None)
    if pr is None or pr["status"] != "open":
        raise ValueError(f"Unknown or closed pull request: {pr_id}")
    pr["reviews"].append({"reviewer": reviewer, "decision": decision, "evidence": evidence.strip()})
    pr["review"] = decision


def merge_pull_request(state: dict[str, Any], pr_id: str) -> None:
    if state["gate_index"] >= 3:
        raise ValueError("Project scope is frozen after pre-production approval.")
    pr = next((item for item in state["pull_requests"] if item["id"] == pr_id), None)
    if pr is None or pr["status"] != "open":
        raise ValueError(f"Unknown or closed pull request: {pr_id}")
    if pr.get("review") != "approved":
        raise ValueError(f"Pull request {pr_id} cannot merge until Michael approves it.")
    pr["status"] = "merged"
    state["validation"] = None


def record_validation(state: dict[str, Any], tester: str, passed: bool, evidence: str) -> None:
    if tester != "eleanor":
        raise ValueError("Eleanor must independently validate the release candidate.")
    if state["gate_index"] != 2:
        raise ValueError("QA validation is only available before pre-production approval.")
    if not state["tasks"] or any(t["status"] != "done" for t in state["tasks"]):
        raise ValueError("QA validation requires implementation tasks to be complete.")
    if any(pr["status"] == "open" for pr in state["pull_requests"]):
        raise ValueError("QA validation requires every pull request to be closed or merged.")
    if not evidence.strip():
        raise ValueError("QA validation requires recorded evidence.")
    result = {"tester": tester, "passed": passed, "evidence": evidence.strip()}
    state["validation_history"].append(result)
    state["validation"] = result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".workforce/state.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    project = sub.add_parser("project")
    project.add_argument("name")
    project.add_argument("objective")
    task = sub.add_parser("task")
    task.add_argument("id")
    task.add_argument("title")
    task.add_argument("--agent", required=True)
    task.add_argument("--depends-on", nargs="*", default=[])
    task.add_argument("--kind", choices=("code", "non_code"), default="code")
    claim = sub.add_parser("claim")
    claim.add_argument("id")
    claim.add_argument("--agent", required=True)
    retry = sub.add_parser("retry")
    retry.add_argument("id")
    retry.add_argument("--reason", required=True)
    retry.add_argument("--agent")
    pr_open = sub.add_parser("pr-open")
    pr_open.add_argument("id")
    pr_open.add_argument("--title", required=True)
    pr_open.add_argument("--task", required=True)
    pr_review = sub.add_parser("pr-review")
    pr_review.add_argument("id")
    pr_review.add_argument("--reviewer", required=True)
    pr_review.add_argument("--decision", choices=("approved", "changes_requested"), required=True)
    pr_review.add_argument("--evidence", required=True)
    pr_merge = sub.add_parser("pr-merge")
    pr_merge.add_argument("id")
    pr_close = sub.add_parser("pr-close")
    pr_close.add_argument("id")
    pr_close.add_argument("--reason", required=True)
    validation = sub.add_parser("validate")
    validation.add_argument("--tester", required=True)
    validation.add_argument("--result", choices=("passed", "failed"), required=True)
    validation.add_argument("--evidence", required=True)
    sub.add_parser("status")
    approve = sub.add_parser("approve")
    approve.add_argument("gate", choices=GATES)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--evidence", required=True)
    done = sub.add_parser("done")
    done.add_argument("id")
    args = parser.parse_args()
    try:
        with state_lock(args.state):
            state = load(args.state)
            if args.command == "project":
                create_project(state, args.name, args.objective)
            elif args.command == "task":
                add_task(state, args.id, args.title, args.agent, args.depends_on, args.kind)
            elif args.command == "claim":
                claim_task(state, args.id, args.agent)
            elif args.command == "retry":
                retry_task(state, args.id, args.reason, args.agent)
            elif args.command == "approve":
                approve_gate(state, args.gate, args.approver, args.evidence)
            elif args.command == "done":
                mark_done(state, args.id)
            elif args.command == "pr-open":
                open_pull_request(state, args.id, args.title, args.task)
            elif args.command == "pr-review":
                review_pull_request(state, args.id, args.reviewer, args.decision, args.evidence)
            elif args.command == "pr-merge":
                merge_pull_request(state, args.id)
            elif args.command == "pr-close":
                close_pull_request(state, args.id, args.reason)
            elif args.command == "validate":
                record_validation(state, args.tester, args.result == "passed", args.evidence)
            if args.command == "status":
                print(json.dumps({**state, "next_gate": GATES[state["gate_index"]]
                                  if state["gate_index"] < len(GATES) else None,
                                  "ready_tasks": ready_tasks(state)}, indent=2))
            else:
                save(args.state, state)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
