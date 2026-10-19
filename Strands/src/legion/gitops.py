"""Local git worktrees and code-host adapters (local git or GitHub via `gh`)."""
from __future__ import annotations

import re
import shlex
import subprocess
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .store import slugify

# (argv, cwd, timeout) -> CompletedProcess; injectable so tests never shell out
Runner = Callable[[list[str], Path, int], subprocess.CompletedProcess]
_CHECK_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "VIRTUAL_ENV")


def run_process(argv: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    # stdin is closed so agent-run commands can never reach an interactive human prompt (e.g. gate approval)
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def run_check_process(argv: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """Run repository-controlled checks without exposing orchestrator credentials."""
    env = {key: os.environ[key] for key in _CHECK_ENV_ALLOWLIST if key in os.environ}
    home = Path(tempfile.gettempdir()) / "legion-agent-home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, env=env)


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, repo: Path, worktree_root: Path, base_branch: str = "main",
                 integration_branch: str = "legion/integration", runner: Runner = run_process):
        self.repo, self.root = Path(repo).resolve(), Path(worktree_root).resolve()
        self.base, self.integration = base_branch, integration_branch
        self._run = runner

    def git(self, *args: str, cwd: Path | None = None, check: bool = True, timeout: int = 120) -> str:
        # Hooks live in the shared .git, which agent-written scripts could modify: never run them.
        p = self._run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd or self.repo, timeout)
        if check and p.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()}")
        return p.stdout

    def _branch_exists(self, name: str) -> bool:
        return self._run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                         self.repo, 30).returncode == 0

    # -- worktrees -----------------------------------------------------------------------------------
    def integration_worktree(self) -> Path:
        """A dedicated checkout of the integration branch, so the user's own checkout is never touched."""
        path = self.root / "_integration"
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            if self._branch_exists(self.integration):
                self.git("worktree", "add", str(path), self.integration)
            else:
                self.git("worktree", "add", "-b", self.integration, str(path), self.base)
        return path

    def task_worktree(self, task_id: str, title: str) -> tuple[str, Path]:
        branch = f"legion/{task_id}-{slugify(title)[:40]}"
        path = self.root / task_id
        if not path.exists():
            self.integration_worktree()
            self.root.mkdir(parents=True, exist_ok=True)
            if self._branch_exists(branch):
                self.git("worktree", "add", str(path), branch)
            else:
                self.git("worktree", "add", "-b", branch, str(path), self.integration)
        return branch, path

    def commit_all(self, path: Path, message: str) -> bool:
        """Commit anything the agent left uncommitted. Returns True if a commit was made."""
        self.git("add", "-A", cwd=path)
        if not self.git("status", "--porcelain", cwd=path).strip():
            return False
        self.git("-c", "user.name=Legion", "-c", "user.email=legion@localhost",
                 "commit", "-m", message, cwd=path)
        return True

    def has_changes_vs_integration(self, branch: str) -> bool:
        return bool(self.git("rev-list", "--count", f"{self.integration}..{branch}").strip() != "0")

    def diff_vs_integration(self, branch: str, max_chars: int = 60_000) -> str:
        out = self.git("diff", f"{self.integration}...{branch}")
        return out if len(out) <= max_chars else out[:max_chars] + "\n... [diff truncated]"

    def run_checks(self, path: Path, command: str, timeout: int) -> tuple[bool, str]:
        if not command.strip():
            return True, "no check command configured"
        try:
            p = run_check_process(shlex.split(command), path, timeout)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return False, f"check could not complete: {exc}"
        return p.returncode == 0, (p.stdout + p.stderr)[-4000:]

    def merge_into_integration(self, branch: str, message: str) -> None:
        wt = self.integration_worktree()
        self.git("-c", "user.name=Legion", "-c", "user.email=legion@localhost",
                 "merge", "--no-ff", branch, "-m", message, cwd=wt)


# -- code hosts ------------------------------------------------------------------------------------------
@dataclass
class PullRequestRef:
    number: int | None
    url: str | None


class CodeHost(Protocol):
    def open_pr(self, branch: str, title: str, body: str) -> PullRequestRef: ...
    def post_review(self, ref: PullRequestRef, verdict: str, body: str) -> None: ...
    def merge(self, ref: PullRequestRef, branch: str, title: str,
              expected_head: str | None = None) -> None: ...


class LocalCodeHost:
    """No remote: the PR lives in the markdown ledger and merge is a local git merge."""

    def __init__(self, git: Git):
        self.git = git

    def open_pr(self, branch, title, body):
        return PullRequestRef(None, None)

    def post_review(self, ref, verdict, body):
        pass

    def merge(self, ref, branch, title, expected_head=None):
        self.git.merge_into_integration(branch, title)


class GitHubCodeHost:
    """GitHub via the `gh` CLI. Reviews are posted as PR comments: GitHub forbids approving your own PR
    from the same account, so the approval that matters is recorded by the engine, which alone can
    authorise `merge`."""

    def __init__(self, git: Git, repo: str):
        self.git, self.repo = git, repo

    def _gh(self, *args: str, cwd: Path | None = None) -> str:
        p = self.git._run(["gh", *args, "--repo", self.repo], cwd or self.git.repo, 120)
        if p.returncode != 0:
            raise GitError(f"gh {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()}")
        return p.stdout.strip()

    def open_pr(self, branch, title, body):
        self.git.git("push", "-u", "origin", branch)  # branches are shared across worktrees
        self.git.git("push", "-u", "origin", self.git.integration, check=False)  # PR base must exist remotely
        url = self._gh("pr", "create", "--base", self.git.integration, "--head", branch,
                       "--title", title, "--body", body)
        m = re.search(r"/pull/(\d+)", url)
        return PullRequestRef(int(m.group(1)) if m else None, url)

    def post_review(self, ref, verdict, body):
        if ref.number:
            self._gh("pr", "comment", str(ref.number), "--body", f"**Michael — {verdict}**\n\n{body}")

    def push_current_branch(self, branch: str, cwd: Path) -> None:
        """Publish the latest locally reviewed task commit before requesting a remote merge."""
        self.git.git("push", "-u", "origin", branch, cwd=cwd)

    def merge(self, ref, branch, title, expected_head=None):
        if not ref.number:
            raise GitError("No PR number to merge.")
        if not expected_head:
            raise GitError("An expected head SHA is required to merge an approved PR.")
        self._gh("pr", "merge", str(ref.number), "--squash", "--subject", title,
                 "--match-head-commit", expected_head)
        wt = self.git.integration_worktree()
        self.git.git("pull", "--ff-only", "origin", self.git.integration, cwd=wt)
