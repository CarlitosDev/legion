"""Human interface. Gate approvals live here and need an interactive terminal; agents cannot reach them."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from .engine import GATES, Workflow, WorkflowError
from .gitops import Git, GitHubCodeHost, LocalCodeHost
from .roster import ROSTER
from .runner import Runner, TaskError
from .scheduler import Orchestrator
from .settings import Settings
from .store import Store

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Legion: autonomous workforce on AWS Strands.")
GATE_ALIASES = {"project": GATES[0], "design": GATES[1], "preproduction": GATES[2], "production": GATES[3]}
ProjectOpt = typer.Option(None, "--project", "-p", envvar="LEGION_PROJECT", help="Project slug (if several exist).")


def _settings() -> Settings:
    return Settings()


def _store(settings: Settings, project: Optional[str]) -> Store:
    root = settings.state_dir
    if project is None:
        found = sorted(p.name for p in root.glob("*") if (p / "project.md").exists()) if root.exists() else []
        if len(found) != 1:
            raise typer.BadParameter(f"Pass --project; found: {', '.join(found) or 'no projects'}.")
        project = found[0]
    return Store(root / project)


def _git(settings: Settings, store: Store) -> Git | None:
    repo = settings.repo_path
    if repo is None:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if p.returncode != 0:
            return None
        repo = Path(p.stdout.strip())
    return Git(repo, store.root / "worktrees", settings.base_branch, settings.integration_branch)


def _build(project: Optional[str]) -> tuple[Settings, Workflow, Runner]:
    settings = _settings()
    store = _store(settings, project)
    wf = Workflow(store, settings)
    git = _git(settings, store)
    host = None
    if git:
        host = GitHubCodeHost(git, settings.github_repo) if settings.github_enabled else LocalCodeHost(git)
    if settings.jira_enabled:
        from .jira import JiraClient, attach
        attach(wf, JiraClient(settings.jira_base_url, settings.jira_project, settings.jira_issue_type), settings)
    from .agents import StrandsInvoker
    return settings, wf, Runner(wf, settings, StrandsInvoker(settings), git, host)


def _guard(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (WorkflowError, TaskError) as exc:
            typer.secho(f"Refused: {exc}", fg="red", err=True)
            raise typer.Exit(1)
    return wrapper


@app.command()
@_guard
def new(name: str, objective: str = typer.Argument(..., help="Brief text, or a path to a .md/.txt file."),
        educational: bool = typer.Option(False, help="Activates Will (learning design).")):
    """Create a project."""
    settings = _settings()
    text = Path(objective).read_text() if Path(objective).is_file() else objective
    from .store import slugify
    wf = Workflow(Store(settings.state_dir / slugify(name)), settings)
    slug = wf.create_project(name, text, educational)
    typer.echo(f"Created project '{slug}'. Next: legion plan discovery -p {slug}")


@app.command()
@_guard
def roster():
    """Show the 13 agents and the Bedrock model each resolves to."""
    s = _settings()
    for a in ROSTER.values():
        try:
            model = s.model_id_for(a.id)
        except ValueError as exc:
            model = f"NOT CONFIGURED ({exc})"
        typer.echo(f"{a.name:<8} {a.title:<40} {model}  timeout={s.timeout_for(a.id)}s")


@app.command()
@_guard
def status(project: Optional[str] = ProjectOpt):
    """Project, gate, task, escalation and budget summary."""
    s = _settings()
    st = Workflow(_store(s, project), s).status()
    for k, v in st.items():
        typer.echo(f"{k:>16}: {v}")


@app.command()
@_guard
def tasks(project: Optional[str] = ProjectOpt):
    """List tasks."""
    s = _settings()
    for t in Workflow(_store(s, project), s).tasks():
        typer.echo(f"{t['id']:<6} {t['status']:<12} {t['stage']:<15} {t['agent']:<8} {t['title']}"
                   + (f"  [{t['jira_key']}]" if t.get("jira_key") else ""))


@app.command()
@_guard
def plan(stage: str, guidance: str = typer.Option("", help="Extra direction for Ed."),
         project: Optional[str] = ProjectOpt):
    """Ed plans tasks for a stage (discovery, design, implementation, release...)."""
    _, _, runner = _build(project)
    for tid in runner.plan(stage, guidance):
        typer.echo(f"created {tid}")


@app.command("task-add")
@_guard
def task_add(title: str, agent: str, stage: str, kind: str = "non_code",
             depends_on: list[str] = typer.Option([], "--depends-on"),
             acceptance: list[str] = typer.Option([], "--acceptance"), description: str = "",
             project: Optional[str] = ProjectOpt):
    """Add a task by hand."""
    s = _settings()
    typer.echo(Workflow(_store(s, project), s).add_task(title, agent, stage, description=description, kind=kind,
                                                      depends_on=depends_on, acceptance=acceptance))


@app.command()
@_guard
def run(max_tasks: Optional[int] = typer.Option(None, help="Stop launching after N tasks."),
        project: Optional[str] = ProjectOpt):
    """Run ready tasks in parallel until a gate, escalation or the budget stops the workforce."""
    settings, wf, runner = _build(project)
    summary = Orchestrator(wf, runner, settings, emit=typer.echo).run(max_tasks)
    typer.echo(f"completed={summary.completed} failed={summary.failed} recovered={summary.recovered}")
    nxt = wf.status()["next_gate"]
    if nxt and not wf.runnable():
        typer.echo(f"Idle. Review the evidence, then: legion approve {nxt} (gate ready only if its tasks are done).")


@app.command()
@_guard
def approve(gate: str, approver: str = typer.Option(..., help="Your name, recorded in the audit trail."),
            evidence: str = typer.Option(..., help="What you reviewed and decided."),
            project: Optional[str] = ProjectOpt):
    """Approve the next gate. Requires an interactive terminal and a typed confirmation."""
    gate = GATE_ALIASES.get(gate, gate)
    if not sys.stdin.isatty():
        typer.secho("Refused: approvals need an interactive terminal.", fg="red", err=True)
        raise typer.Exit(1)
    s = _settings()
    wf = Workflow(_store(s, project), s)
    typer.echo(f"Gate: {gate}\nApprover: {approver}\nEvidence: {evidence}")
    if not typer.confirm("Record this approval?"):
        raise typer.Exit(1)
    wf.approve_gate(gate, approver, evidence)
    typer.echo("Approved.")


@app.command()
@_guard
def escalations(project: Optional[str] = ProjectOpt):
    """List escalations awaiting you."""
    s = _settings()
    for e in Workflow(_store(s, project), s).store.list("escalations"):
        typer.echo(f"{e['id']} [{e['status']}] {e['kind']} (from {e['raised_by']}, task {e.get('task_id')})\n"
                   f"    {e.body.strip().splitlines()[0] if e.body.strip() else ''}")


@app.command()
@_guard
def resolve(escalation_id: str, by: str = typer.Option(...), resolution: str = typer.Option(...),
            project: Optional[str] = ProjectOpt):
    """Resolve an escalation with an explicit decision."""
    s = _settings()
    Workflow(_store(s, project), s).resolve_escalation(escalation_id, by, resolution)
    typer.echo("Resolved.")


@app.command()
@_guard
def retry(task_id: str, by: str = typer.Option(...), reason: str = typer.Option(...),
          project: Optional[str] = ProjectOpt):
    """Reset a failed task so it can run again."""
    s = _settings()
    Workflow(_store(s, project), s).retry(task_id, by, reason)
    typer.echo(f"{task_id} is ready again.")


@app.command("jira-backfill")
@_guard
def jira_backfill(project: Optional[str] = ProjectOpt):
    """Create Jira issues for tasks that have none (requires LEGION_JIRA_ENABLED=true and JIRA_* env)."""
    settings, wf, _ = _build(project)
    if not settings.jira_enabled:
        raise typer.BadParameter("Set LEGION_JIRA_ENABLED=true.")
    for t in wf.tasks():
        wf._emit("tasks", t)
        typer.echo(f"{t['id']} -> {wf.task(t['id']).get('jira_key')}")


if __name__ == "__main__":
    app()
