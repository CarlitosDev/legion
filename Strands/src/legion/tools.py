"""Strands tools handed to agents. Each agent gets only the groups listed in its AgentSpec (least privilege).

Tools call the Workflow engine, which owns every rule. They return "ERROR: ..." strings rather than raising,
so a model can adapt to a rejected action. File/shell tools are confined to the task's worktree; this is a
guard-rail, not a sandbox (see README).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from strands import tool

from .engine import STAGES, Workflow, WorkflowError
from .gitops import Git
from .roster import ROSTER, AgentSpec
from .settings import Settings


class Cancelled(RuntimeError):
    """The task's time budget expired or the run was stopped."""


@dataclass
class ToolContext:
    spec: AgentSpec
    workflow: Workflow
    settings: Settings
    task_id: str | None = None
    workdir: Path | None = None
    branch: str | None = None
    git: Git | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    consult: Callable[[str, str, "ToolContext"], str] | None = None

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("time budget exhausted; stop and summarise what you have")


_BLOCKED_PROGRAMS = {"gh", "ssh", "scp", "sudo", "su", "curl", "wget", "aws", "legion"}
_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "csh", "ksh", "env", "xargs", "nohup"}
_INLINE_CODE_FLAGS = {"-c", "-e", "-E", "--eval", "-r", "--exec"}
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "VIRTUAL_ENV")
_GIT_UNSAFE_OPTIONS = {"-c", "-C", "--namespace"}
_BLOCKED_GIT = {"push", "merge", "remote", "worktree", "config", "rebase", "checkout", "switch", "reset",
                "clean", "branch", "fetch", "pull"}


def guard_command(argv: list[str]) -> str | None:
    """Return a reason if the command is outside what an agent may run, else None."""
    if not argv:
        return "empty command"
    prog = Path(argv[0]).name
    if prog in _BLOCKED_PROGRAMS:
        return f"'{prog}' is not available to agents"
    if prog in _SHELLS:
        return f"'{prog}' is not available to agents; run the program directly"
    if prog.startswith(("python", "node", "perl", "ruby")) and any(a in _INLINE_CODE_FLAGS for a in argv[1:]):
        return "inline code (-c/-e) is not available to agents; put code in a file in the workspace"
    if prog == "git":
        # Options that smuggle in aliases or another repo/worktree defeat the subcommand check: refuse them.
        for a in argv[1:]:
            if a in _GIT_UNSAFE_OPTIONS or a.startswith(("--git-dir", "--work-tree", "--exec-path")):
                return f"'git {a}' is not available to agents"
        if bad := next((a for a in argv[1:] if a in _BLOCKED_GIT), None):
            return f"'git {bad}' is reserved for the orchestrator (branching, merging and pushing are not agent actions)"
    return None


def scrubbed_env() -> dict[str, str]:
    """Children never inherit AWS/Jira/GitHub credentials: only an allowlist of harmless variables."""
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    home = Path(tempfile.gettempdir()) / "legion-agent-home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return env


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() != p and root.resolve() not in p.parents:
        raise WorkflowError(f"Path escapes the workspace: {rel}")
    return p


def _safe(fn):
    """Convert rule violations and usage errors into messages the model can act on; let Cancelled propagate."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Cancelled:
            raise
        except (WorkflowError, OSError, KeyError, ValueError) as exc:
            return f"ERROR: {exc}"
    return wrapper


def build_tools(ctx: ToolContext) -> list:
    wf, spec = ctx.workflow, ctx.spec
    groups = spec.tools
    tools: list = []

    def add(fn):
        tools.append(tool(_safe(fn)))
        return fn

    # -- everyone but the reviewer -------------------------------------------------------------------------
    if groups - {"review"}:
        def record_decision(text: str) -> str:
            """Record a decision that affects other tasks or people, against the current task."""
            ctx.check()
            return f"Recorded {wf.record_decision(spec.id, text, ctx.task_id)}"

        def escalate(kind: str, summary: str) -> str:
            """Escalate to the human owner: scope change, budget, architecture change, unresolved high-impact
            disagreement, or critical security/operational risk. Kind is a short label such as 'scope_change'."""
            ctx.check()
            return f"Escalated as {wf.escalate(kind, summary, spec.id, ctx.task_id)}; the owner will respond."

        def project_overview() -> str:
            """Read the project objective, tasks with status, and recorded decisions."""
            ctx.check()
            rows = [f"{t['id']} [{t['stage']}/{t['status']}] {t['agent']}: {t['title']}" for t in wf.tasks()]
            decs = [f"{d['id']} ({d['author']}): {d.body.strip()[:200]}" for d in wf.store.list("decisions")]
            return f"OBJECTIVE:\n{wf.objective()}\n\nTASKS:\n" + "\n".join(rows) + "\n\nDECISIONS:\n" + "\n".join(decs)

        for f in (record_decision, escalate, project_overview):
            add(f)

    if "docs" in groups:
        def write_document(name: str, content: str) -> str:
            """Write or replace a markdown document in the project's docs folder (name like 'api-contract.md')."""
            ctx.check()
            path = _inside(wf.store.root / "docs", name if name.endswith(".md") else name + ".md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return f"Wrote docs/{path.name}"

        def read_document(name: str) -> str:
            """Read a document from the project's docs folder."""
            ctx.check()
            return _inside(wf.store.root / "docs", name if name.endswith(".md") else name + ".md").read_text()[:20000]

        add(write_document); add(read_document)

    if "consult" in groups and ctx.consult:
        def ask_colleague(agent_id: str, question: str) -> str:
            """Ask another agent a focused question (API contract, data shape, feasibility). Record the outcome
            with record_decision if it affects the work."""
            ctx.check()
            if agent_id not in ROSTER or agent_id == spec.id:
                return f"ERROR: unknown colleague '{agent_id}'"
            return ctx.consult(agent_id, question, ctx)

        add(ask_colleague)

    # -- workspace access ----------------------------------------------------------------------------------
    if groups & {"code", "qa", "review"} and ctx.workdir:
        root = ctx.workdir

        def list_files(subdir: str = ".") -> str:
            """List files under a directory of the workspace (skips .git)."""
            ctx.check()
            base = _inside(root, subdir)
            files = sorted(str(p.relative_to(root)) for p in base.rglob("*")
                           if p.is_file() and ".git" not in p.parts)
            return "\n".join(files[:500]) or "(empty)"

        def read_file(path: str) -> str:
            """Read a text file from the workspace."""
            ctx.check()
            return _inside(root, path).read_text(errors="replace")[:40000]

        add(list_files); add(read_file)

        if groups & {"code", "qa"}:
            def run_command(command: str) -> str:
                """Run a command (no shell) in the workspace, e.g. 'python -m pytest -q'. Branching, merging,
                pushing and network/cloud CLIs are not permitted."""
                ctx.check()
                argv = shlex.split(command)
                if reason := guard_command(argv):
                    return f"ERROR: {reason}"
                try:
                    p = subprocess.run(argv, cwd=root, capture_output=True, text=True, env=scrubbed_env(),
                                       timeout=ctx.settings.shell_timeout_s, stdin=subprocess.DEVNULL)
                except Exception as exc:  # timeouts, missing binaries
                    return f"ERROR: {exc}"
                return f"exit {p.returncode}\n{(p.stdout + p.stderr)[-6000:]}"

            add(run_command)

    if "code" in groups and ctx.workdir:
        def write_file(path: str, content: str) -> str:
            """Create or overwrite a file in the workspace."""
            ctx.check()
            p = _inside(ctx.workdir, path)
            if ".git" in p.relative_to(ctx.workdir.resolve()).parts:
                return "ERROR: .git is off limits"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"Wrote {path} ({len(content)} chars)"

        def resolve_finding(pr_id: str, finding_id: int, how_addressed: str) -> str:
            """After fixing (or explaining) a review finding on your pull request, mark it resolved. Michael
            re-reviews; unresolved critical/major findings block approval."""
            ctx.check()
            wf.resolve_finding(pr_id, finding_id, spec.id, how_addressed)
            return f"{pr_id} finding {finding_id} marked resolved"

        def challenge_decision(decision_id: str, new_evidence: str) -> str:
            """Challenge an approved baseline decision. Requires substantial new evidence; goes to the human."""
            ctx.check()
            return f"Escalated as {wf.challenge_decision(decision_id, spec.id, new_evidence)}"

        for f in (write_file, resolve_finding, challenge_decision):
            add(f)

    if "review" in groups and ctx.git and ctx.branch:
        def read_diff() -> str:
            """Read the pull request diff against the integration branch."""
            ctx.check()
            return ctx.git.diff_vs_integration(ctx.branch)

        add(read_diff)

    # -- role-specific -------------------------------------------------------------------------------------
    if "plan" in groups:
        def create_task(title: str, assignee: str, stage: str, description: str = "", kind: str = "non_code",
                        depends_on: list[str] | None = None, acceptance: list[str] | None = None) -> str:
            ctx.check()
            tid = wf.add_task(title, assignee, stage, description=description, kind=kind, depends_on=depends_on,
                              acceptance=acceptance, created_by=spec.id)
            return f"Created {tid}"

        create_task.__doc__ = (
            "Create a task inside the approved scope. stage is one of: " + ", ".join(STAGES) + ". "
            "kind is 'code' (needs acceptance criteria) or 'non_code'. depends_on lists existing task ids.")
        add(create_task)

    if "idea" in groups:
        def record_idea(title: str, text: str) -> str:
            """File a speculative idea in the innovation backlog. Never interrupt committed delivery with ideas."""
            ctx.check()
            return f"Filed {wf.add_idea(spec.id, title, text)} in the innovation backlog"

        add(record_idea)

    return tools
