import json
import subprocess

import httpx
import pytest
from typer.testing import CliRunner

from legion import jira
from legion.cli import app
from legion.gitops import Git, GitHubCodeHost, PullRequestRef
from legion.tools import guard_command


def test_jira_mirror_creates_transitions_and_comments(wf, settings):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.url.path.endswith("/transitions") and req.method == "GET":
            return httpx.Response(200, json={"transitions": [
                {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
                {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
                {"id": "31", "name": "Done", "to": {"name": "Done"}}]})
        if req.url.path == "/rest/api/3/issue":
            return httpx.Response(201, json={"key": f"DATA-{len([c for c in calls if c[1].endswith('/issue')])}"})
        return httpx.Response(204)

    client = jira.JiraClient("https://x.atlassian.net", "DATA", client=httpx.Client(
        base_url="https://x.atlassian.net", transport=httpx.MockTransport(handler)))
    jira.attach(wf, client, settings)

    d = wf.add_task("Proposal", "renato", "discovery")
    task = wf.task(d)
    assert task["jira_key"] == "DATA-1"
    created = next(c for c in calls if c[0] == "POST" and c[1] == "/rest/api/3/issue")[2]["fields"]
    assert created["project"] == {"key": "DATA"} and "agent-renato" in created["labels"]
    a = wf.claim(d, "renato")
    wf.record_decision("renato", "Target B2C first", task_id=d)
    wf.complete(d, a, "ok", "docs/x.md")
    posts = [c[1] for c in calls if c[0] == "POST"]
    assert any(p.endswith("/DATA-1/comment") for p in posts)
    assert [c[2]["transition"]["id"] for c in calls if c[1].endswith("/transitions") and c[0] == "POST"] == ["11", "21", "31"]
    assert wf.task(d)["jira_status"] == "Done"


def test_jira_failure_never_breaks_enforcement(wf, settings):
    class Boom:
        def create_issue(self, *a): raise RuntimeError("jira down")
        def transition(self, *a): ...
        def comment(self, *a): ...

    jira.attach(wf, Boom(), settings)
    d = wf.add_task("Proposal", "renato", "discovery")           # still created
    assert wf.task(d)["status"] == "ready" or wf.task(d)["status"] == "blocked"
    assert "listener failed" in (wf.store.root / "audit.md").read_text()


def test_github_host_issues_expected_gh_commands(tmp_path):
    seen = []

    def runner(argv, cwd, timeout):
        seen.append(argv)
        out = "https://github.com/CarlitosDev/legion/pull/7\n" if argv[:3] == ["gh", "pr", "create"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    git = Git(tmp_path, tmp_path / "wt", "main", "legion/integration", runner=runner)
    host = GitHubCodeHost(git, "CarlitosDev/legion")
    ref = host.open_pr("legion/T-3-api", "T-3: API", "body")
    assert ref == PullRequestRef(7, "https://github.com/CarlitosDev/legion/pull/7")
    create = next(a for a in seen if a[:3] == ["gh", "pr", "create"])
    assert create[create.index("--base") + 1] == "legion/integration" and "--repo" in create
    host.post_review(ref, "approved", "lgtm")
    host.merge(ref, "legion/T-3-api", "T-3: API")
    flat = [" ".join(a) for a in seen]
    assert any(f.startswith("gh pr comment 7") for f in flat) and any("pr merge 7 --squash" in f for f in flat)


@pytest.mark.parametrize("cmd,blocked", [
    ("git push origin main", True), ("bash -c ls", True), ("python -c print(1)", True), ("python3.13 -c x", True), ("env sh", True), ("git -c alias.x=!id x", True), ("git --git-dir=/tmp/x log", True), ("git -C x merge feature", True), ("git checkout main", True),
    ("gh pr merge 1", True), ("legion approve project", True), ("curl http://x", True),
    ("git status", False), ("git add -A", False), ("python -m pytest -q", False), ("ls -la", False)])
def test_command_guard(cmd, blocked):
    import shlex
    assert (guard_command(shlex.split(cmd)) is not None) is blocked


def test_cli_approval_refuses_without_a_terminal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LEGION_STATE_DIR", str(tmp_path / "state"))
    r = CliRunner()
    assert r.invoke(app, ["new", "Demo", "Build a thing"]).exit_code == 0
    out = r.invoke(app, ["approve", "project", "--approver", "me", "--evidence", "ok"])
    assert out.exit_code == 1 and "interactive terminal" in out.output
    assert "gates_approved: 0" in r.invoke(app, ["status"]).output


def test_store_files_are_readable_markdown(wf):
    d = wf.add_task("Proposal", "renato", "discovery", description="Scope the thing")
    text = (wf.store.root / "tasks" / f"{d}.md").read_text()
    assert text.startswith("---\n") and "title: Proposal" in text and "- " in text.split("---")[2]
    assert (wf.store.root / "project.md").read_text().rstrip().endswith("Build a thing")
    with pytest.raises(ValueError):
        wf.store.path("tasks", "../../etc/passwd")


def test_agent_commands_do_not_inherit_secrets(wf, settings, tmp_path, monkeypatch):
    from legion.roster import ROSTER
    from legion.tools import ToolContext, build_tools
    monkeypatch.setenv("JIRA_API_TOKEN", "s3cret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s3cret")
    script = tmp_path / "show.py"
    script.write_text("import os; print(sorted(k for k in os.environ if 'JIRA' in k or 'AWS' in k or 'GH_' in k))")
    ctx = ToolContext(ROSTER["teddy"], wf, settings, workdir=tmp_path)
    run = next(t for t in build_tools(ctx) if t.tool_name == "run_command")
    import sys
    out = run(command=f"{sys.executable} show.py")
    assert "[]" in out and "s3cret" not in out
