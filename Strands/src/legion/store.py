"""Markdown-file persistence: one file per record, YAML front matter + free-text body.

Layout under <state_dir>/<project-slug>/:
    project.md  audit.md  tasks/*.md  prs/*.md  decisions/*.md  escalations/*.md
    approvals/*.md  validations/*.md  backlog/*.md  docs/*.md
"""
from __future__ import annotations

import fcntl
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

KINDS = ("tasks", "prs", "decisions", "escalations", "approvals", "validations", "backlog", "docs")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


class Record(dict):
    """Front-matter dict with a markdown `body` attribute."""

    body: str = ""

    def __init__(self, data: dict[str, Any] | None = None, body: str = ""):
        super().__init__(data or {})
        self.body = body


class Store:
    def __init__(self, project_dir: Path):
        self.root = Path(project_dir)
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd = None

    # -- locking: thread-reentrant + cross-process -------------------------------------------
    @contextmanager
    def lock(self) -> Iterator[None]:
        with self._rlock:
            if self._depth == 0:
                self.root.mkdir(parents=True, exist_ok=True)
                self._fd = (self.root / ".lock").open("a")
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                    self._fd.close()
                    self._fd = None

    # -- records -------------------------------------------------------------------------------
    def path(self, kind: str, rid: str | None = None) -> Path:
        if rid is None:
            return self.root / f"{kind}.md"
        if not _SAFE_ID.match(rid):
            raise ValueError(f"Unsafe record id: {rid!r}")
        return self.root / kind / f"{rid}.md"

    def exists(self, kind: str, rid: str | None = None) -> bool:
        return self.path(kind, rid).exists()

    def read(self, kind: str, rid: str | None = None) -> Record:
        text = self.path(kind, rid).read_text()
        if not text.startswith("---\n"):
            raise ValueError(f"{kind}/{rid}: missing front matter")
        head, _, body = text[4:].partition("\n---\n")
        return Record(yaml.safe_load(head) or {}, body.lstrip("\n"))

    def write(self, kind: str, rid: str | None, rec: Record) -> None:
        path = self.path(kind, rid)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "---\n" + yaml.safe_dump(dict(rec), sort_keys=False, allow_unicode=True) + "---\n\n" + rec.body
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text if text.endswith("\n") else text + "\n")
        tmp.replace(path)

    def list(self, kind: str) -> list[Record]:
        folder = self.root / kind
        def order(path: Path) -> tuple[str, int, str]:
            match = re.fullmatch(r"(.+?)-(\d+)", path.stem)
            return (match.group(1), int(match.group(2)), path.stem) if match else (path.stem, -1, path.stem)
        return [self.read(kind, p.stem) for p in sorted(folder.glob("*.md"), key=order)] if folder.exists() else []

    def next_id(self, kind: str, prefix: str) -> str:
        folder = self.root / kind
        nums = [int(m.group(1)) for p in folder.glob(f"{prefix}-*.md")
                if (m := re.fullmatch(rf"{prefix}-(\d+)", p.stem))] if folder.exists() else []
        return f"{prefix}-{max(nums, default=0) + 1}"

    def log(self, rec: Record, line: str) -> None:
        """Append a timestamped line to a record's body log."""
        rec.body = (rec.body.rstrip("\n") + f"\n- {now()} {line}\n") if rec.body else f"- {now()} {line}\n"

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        path = self.root / "audit.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock(), path.open("a") as f:
            if path.stat().st_size == 0:
                f.write("# Audit log\n\n")
            f.write(f"- {now()} **{actor}** {action}" + (f": {detail}" if detail else "") + "\n")
