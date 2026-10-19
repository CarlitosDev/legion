import pytest

from legion.engine import Workflow
from legion.settings import Settings
from legion.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(state_dir=tmp_path / "state", _env_file=None, max_task_attempts=2, max_review_rounds=2)


@pytest.fixture
def wf(tmp_path, settings):
    w = Workflow(Store(tmp_path / "proj"), settings)
    w.create_project("Demo", "Build a thing")
    return w


def drive_to_implementation(wf):
    """Discovery + design done, both gates approved, one code task ready."""
    d = wf.add_task("Proposal", "renato", "discovery")
    a = wf.claim(d, "renato"); wf.complete(d, a, "ok", "docs/d.md")
    wf.approve_gate("project_approval", "owner", "scope ok")
    g = wf.add_task("Architecture", "hadrian", "design")
    a = wf.claim(g, "hadrian"); wf.complete(g, a, "ok", "docs/g.md")
    c = wf.add_task("Build API", "teddy", "implementation", kind="code", acceptance=["returns 200"])
    wf.approve_gate("technical_design_approval", "owner", "arch ok")
    return c


import subprocess
import sys
import threading

from legion.agents import InvokeResult
from legion.gitops import Git, LocalCodeHost
from legion.runner import Runner
from legion.scheduler import Orchestrator


class FakeInvoker:
    """Scripted stand-in for Bedrock: handlers[agent_id](prompt, ctx, structured) -> str | BaseModel."""

    def __init__(self, **handlers):
        self.handlers, self.calls = handlers, []
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def invoke(self, agent_id, prompt, ctx, structured=None):
        with self._lock:
            self.calls.append((agent_id, structured.__name__ if structured else None))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            out = self.handlers[agent_id](prompt, ctx, structured)
        finally:
            with self._lock:
                self.active -= 1
        if structured is not None:
            return InvokeResult(text="", structured=out, tokens=100)
        return InvokeResult(text=out, tokens=100)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for cmd in (["init", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=r, check=True, capture_output=True)
    (r / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return r


def make_stack(wf, settings, repo, invoker, poll=0.02):
    settings.check_command = f"{sys.executable} -c pass"
    git = Git(repo, wf.store.root / "worktrees", "main", settings.integration_branch)
    runner = Runner(wf, settings, invoker, git, LocalCodeHost(git))
    return runner, Orchestrator(wf, runner, settings, poll_s=poll), git
