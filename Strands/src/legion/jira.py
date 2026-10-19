"""Jira (REST v3) mirror. The markdown ledger drives; Jira is kept in sync as the team-facing source of truth.

Credentials come from JIRA_EMAIL / JIRA_API_TOKEN in the environment and are never written to state.
"""
from __future__ import annotations

import os
from typing import Protocol

import httpx

from .engine import Workflow
from .settings import Settings
from .store import Record


class Tracker(Protocol):
    def create_issue(self, summary: str, description: str, labels: list[str]) -> str: ...
    def transition(self, key: str, status_name: str) -> None: ...
    def comment(self, key: str, text: str) -> None: ...


def adf(text: str) -> dict:
    """Wrap plain text in Atlassian Document Format paragraphs."""
    paras = [p for p in text.split("\n\n") if p.strip()] or [" "]
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": p[:30000]}]} for p in paras]}


class JiraClient:
    def __init__(self, base_url: str, project: str, issue_type: str = "Task",
                 email: str | None = None, token: str | None = None,
                 client: httpx.Client | None = None):
        email, token = email or os.environ.get("JIRA_EMAIL"), token or os.environ.get("JIRA_API_TOKEN")
        if client is None and not (email and token):
            raise RuntimeError("Set JIRA_EMAIL and JIRA_API_TOKEN to enable the Jira mirror.")
        self.project, self.issue_type = project, issue_type
        self.http = client or httpx.Client(base_url=base_url.rstrip("/"), auth=(email, token), timeout=30,
                                           headers={"Accept": "application/json"})

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise RuntimeError(f"Jira {r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
        return r

    def create_issue(self, summary, description, labels):
        r = self._check(self.http.post("/rest/api/3/issue", json={"fields": {
            "project": {"key": self.project}, "issuetype": {"name": self.issue_type},
            "summary": summary[:250], "description": adf(description), "labels": labels}}))
        return r.json()["key"]

    def transition(self, key, status_name):
        r = self._check(self.http.get(f"/rest/api/3/issue/{key}/transitions"))
        for t in r.json()["transitions"]:
            if t["name"].lower() == status_name.lower() or t["to"]["name"].lower() == status_name.lower():
                self._check(self.http.post(f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": t["id"]}}))
                return
        raise RuntimeError(f"{key}: no transition to '{status_name}'")

    def comment(self, key, text):
        self._check(self.http.post(f"/rest/api/3/issue/{key}/comment", json={"body": adf(text)}))


def task_description(t: Record) -> str:
    parts = [f"Agent: {t['agent']} | Stage: {t['stage']} | Kind: {t['kind']}", t.get("description") or ""]
    if t["acceptance"]:
        parts.append("Acceptance criteria:\n" + "\n".join(f"- {a}" for a in t["acceptance"]))
    if t["depends_on"]:
        parts.append("Depends on: " + ", ".join(t["depends_on"]))
    return "\n\n".join(p for p in parts if p)


def attach(workflow: Workflow, tracker: Tracker, settings: Settings) -> None:
    """Subscribe the mirror to workflow events: new tasks create issues; status changes transition them;
    decisions are recorded against the relevant issue."""
    store = workflow.store

    def on_event(kind: str, rec: Record) -> None:
        if kind == "tasks":
            if not rec.get("jira_key"):
                rec["jira_key"] = tracker.create_issue(f"[{rec['id']}] {rec['title']}", task_description(rec),
                                                       ["legion", f"agent-{rec['agent']}", rec["stage"]])
                rec["jira_status"] = None
                store.write("tasks", rec["id"], rec)
            target = settings.jira_status_map.get(rec["status"])
            if target and rec.get("jira_status") != target:
                tracker.transition(rec["jira_key"], target)
                rec["jira_status"] = target
                store.write("tasks", rec["id"], rec)
        elif kind == "decisions" and rec.get("task") and store.exists("tasks", rec["task"]):
            key = store.read("tasks", rec["task"]).get("jira_key")
            if key:
                tracker.comment(key, f"Decision {rec['id']} by {rec['author']}:\n\n{rec.body.strip()}")

    workflow.listeners.append(on_event)
