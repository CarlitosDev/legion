"""Executes one task attempt. Agents do the thinking; this module sequences the mandatory process
(worktree -> PR -> Michael's review -> checks -> merge) so no model can skip a step."""
from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel, Field

from .agents import InvokeResult, Invoker
from .engine import STAGES, Workflow, WorkflowError
from .gitops import CodeHost, Git, PullRequestRef
from .roster import ROSTER
from .settings import Settings
from .tools import Cancelled, ToolContext

Severity = Literal["critical", "major", "minor"]


class Finding(BaseModel):
    severity: Severity
    text: str = Field(description="Specific, evidence-based, actionable. Cite file and line where possible.")


class ReviewVerdict(BaseModel):
    verdict: Literal["approved", "changes_requested"]
    summary: str
    findings: list[Finding] = []


class Defect(BaseModel):
    title: str
    severity: Severity
    description: str


class ValidationReport(BaseModel):
    passed: bool = Field(description="True only if every acceptance criterion is met and no critical defect remains.")
    evidence: str = Field(description="What was run and what it showed.")
    defects: list[Defect] = []


class PlannedTask(BaseModel):
    ref: str = Field(description="Local label other planned tasks use in depends_on.")
    title: str
    assignee: str = Field(description="Agent id, e.g. teddy.")
    stage: str
    kind: Literal["code", "non_code"] = "non_code"
    description: str = ""
    acceptance: list[str] = []
    depends_on: list[str] = Field(default=[], description="Refs of earlier planned tasks or existing task ids.")


class Plan(BaseModel):
    tasks: list[PlannedTask]


class TaskError(RuntimeError):
    """The attempt failed in a way worth retrying (or escalating after max attempts)."""


class Runner:
    def __init__(self, wf: Workflow, settings: Settings, invoker: Invoker,
                 git: Git | None = None, host: CodeHost | None = None):
        self.wf, self.settings, self.invoker, self.git, self.host = wf, settings, invoker, git, host

    # -- plumbing ------------------------------------------------------------------------------------------
    def _ctx(self, agent_id: str, task_id: str | None, cancel: threading.Event, **kw) -> ToolContext:
        return ToolContext(ROSTER[agent_id], self.wf, self.settings, task_id, cancel=cancel,
                           consult=self.consult, git=self.git, **kw)

    def _invoke(self, agent_id: str, prompt: str, ctx: ToolContext, structured=None) -> InvokeResult:
        ctx.check()
        res = self.invoker.invoke(agent_id, prompt, ctx, structured)
        self.wf.record_usage(ctx.task_id, res.tokens)
        ctx.check()
        if res.stop_reason.startswith("limit_"):
            raise TaskError(f"{agent_id} stopped at a budget limit ({res.stop_reason})")
        if structured is not None and not isinstance(res.structured, structured):
            raise TaskError(f"{agent_id} returned no valid {structured.__name__}")
        return res

    def consult(self, agent_id: str, question: str, caller: ToolContext) -> str:
        """Agent-to-agent question. One level deep: the consultee cannot consult further."""
        ctx = ToolContext(ROSTER[agent_id], self.wf, self.settings, caller.task_id, cancel=caller.cancel)
        prompt = (f"{caller.spec.name} ({caller.spec.title}) asks you a focused question while working on "
                  f"{caller.task_id}. Answer briefly and concretely.\n\n{question}")
        return self._invoke(agent_id, prompt, ctx).text

    def _deliverable(self, t, limit: int) -> str:
        rel = t.get("deliverable")
        path = self.wf.store.root / rel if rel else None
        return path.read_text()[:limit] if path and path.is_file() else ""

    def _brief(self, t, extra: str = "") -> str:
        wf = self.wf
        deps = []
        for d in t["depends_on"]:
            dep = wf.task(d)
            deps.append(f"### {d}: {dep['title']} ({dep['status']})\n{self._deliverable(dep, 6000)}")
        decisions = [f"- {d['id']} ({d['author']}{', BASELINE' if d['baseline'] else ''}): {d.body.strip()[:300]}"
                     for d in wf.store.list("decisions")][-25:]
        acc = "\n".join(f"- {a}" for a in t["acceptance"]) or "(none stated)"
        return (f"PROJECT OBJECTIVE\n{wf.objective()}\n\nYOUR TASK {t['id']} [{t['stage']}]: {t['title']}\n"
                f"{t['description']}\n\nACCEPTANCE CRITERIA\n{acc}\n\n"
                f"INPUTS FROM DEPENDENCIES\n{chr(10).join(deps) or '(none)'}\n\n"
                f"RECORDED DECISIONS (settled; do not reopen without substantial new evidence)\n"
                f"{chr(10).join(decisions) or '(none)'}\n\n{extra}")

    # -- entry point ---------------------------------------------------------------------------------------
    def execute(self, task_id: str, attempt: int, cancel: threading.Event) -> None:
        t = self.wf.task(task_id)
        if t["kind"] == "code":
            self._code_task(t, attempt, cancel)
        elif t["stage"] == "validation":
            self._validation_task(t, attempt, cancel)
        else:
            self._document_task(t, attempt, cancel)

    # -- non-code ------------------------------------------------------------------------------------------
    def _document_task(self, t, attempt, cancel) -> None:
        ctx = self._ctx(t["agent"], t["id"], cancel)
        res = self._invoke(t["agent"], self._brief(
            t, "Your final message is the deliverable for this task, in markdown. Make it complete and specific."), ctx)
        if not res.text.strip():
            raise TaskError("empty deliverable")
        rel = f"docs/{t['id']}.md"
        (self.wf.store.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.wf.store.root / rel).write_text(res.text)
        self.wf.complete(t["id"], attempt, res.text[:200].replace("\n", " "), rel)

    # -- code ----------------------------------------------------------------------------------------------
    def _code_task(self, t, attempt, cancel) -> None:
        if not (self.git and self.host):
            raise TaskError("code tasks need a git repository (set LEGION_REPO_PATH)")
        wf, git, s = self.wf, self.git, self.settings
        author = t["agent"]
        branch, path = git.task_worktree(t["id"], t["title"])
        ctx = self._ctx(author, t["id"], cancel, workdir=path, branch=branch)
        pr_id = t.get("pr")
        if not pr_id or wf.store.read("prs", pr_id)["status"] not in ("open", "approved"):
            self._invoke(author, self._brief(t, (
                "You are working in your own git worktree (your current directory for file tools). Implement the task, "
                "write tests, run them with run_command, and leave the work ready for review. Do not branch, merge or push: "
                "the orchestrator opens the pull request. Michael reviews every pull request.")), ctx)
            git.commit_all(path, f"{t['id']}: {t['title']}")
            if not git.has_changes_vs_integration(branch):
                raise TaskError("no changes were produced")
            pr_id = wf.open_pr(t["id"], branch, f"{t['id']}: {t['title']}", author)
            ref = self.host.open_pr(branch, f"{t['id']}: {t['title']}", f"Jira task {t.get('jira_key') or t['id']}\n\n"
                                                                      + "\n".join(f"- {a}" for a in t["acceptance"]))
            pr = wf.store.read("prs", pr_id)
            pr["number"], pr["url"] = ref.number, ref.url
            wf.store.write("prs", pr_id, pr)

        for _ in range(s.max_review_rounds + 2):
            ctx.check()
            ok, checks = git.run_checks(path, s.check_command, s.check_timeout_s)
            if not ok:
                self._fix(t, ctx, pr_id, f"Automated checks failed. Fix them.\n\n{checks}")
                continue
            pr = wf.store.read("prs", pr_id)
            head = git.git("rev-parse", "HEAD", cwd=path).strip()
            verdict = self._review(t, pr, ctx, checks)
            eff = wf.review_pr(pr_id, "michael", verdict.verdict, verdict.summary,
                               [f.model_dump() for f in verdict.findings])
            ref = PullRequestRef(pr["number"], pr["url"])
            self.host.post_review(ref, eff, verdict.summary + "\n\n" + "\n".join(
                f"- **{f.severity}**: {f.text}" for f in verdict.findings))
            if eff == "approved":
                if git.git("rev-parse", "HEAD", cwd=path).strip() != head:
                    continue  # code moved during review; the approval no longer covers it
                wf.authorize_merge(pr_id, True)
                self.host.merge(ref, branch, f"{t['id']}: {t['title']}")
                wf.mark_merged(pr_id)
                wf.complete(t["id"], attempt, f"merged {pr_id}")
                return
            if wf.store.read("prs", pr_id)["status"] == "escalated":
                raise TaskError(f"{pr_id} escalated after repeated review rounds")
            pr = wf.store.read("prs", pr_id)
            todo = "\n".join(f"- finding {f['id']} ({f['severity']}): {f['text']}"
                             for f in pr["findings"] if not f["resolved"])
            self._fix(t, ctx, pr_id, f"Michael requested changes on {pr_id}. Fix each, then call resolve_finding "
                                     f"for it, explaining how it was addressed (or why it does not apply, with "
                                     f"evidence):\n{todo}")
        raise TaskError("pull request did not converge")

    def _fix(self, t, ctx, pr_id, instruction: str) -> None:
        self._invoke(t["agent"], self._brief(t, f"PULL REQUEST {pr_id}\n{instruction}"), ctx)
        self.git.commit_all(ctx.workdir, f"{t['id']}: address review feedback")

    def _review(self, t, pr, ctx, checks: str) -> ReviewVerdict:
        rctx = ToolContext(ROSTER["michael"], self.wf, self.settings, t["id"], workdir=ctx.workdir,
                           branch=ctx.branch, git=self.git, cancel=ctx.cancel)
        prior = "\n".join(f"- #{f['id']} {f['severity']} (round {f['round']}, "
                          f"{'resolved: ' + str(f['resolution']) if f['resolved'] else 'UNRESOLVED'}): {f['text']}"
                          for f in pr["findings"]) or "(none)"
        prompt = (
            f"Review pull request {pr['id']}: {pr['title']} (author: {pr['author']}, round {pr['round'] + 1}).\n\n"
            f"ACCEPTANCE CRITERIA\n" + "\n".join(f"- {a}" for a in t["acceptance"]) +
            f"\n\nAUTOMATED CHECKS (passing)\n{checks[-1500:]}\n\nPRIOR FINDINGS\n{prior}\n\nDIFF\n"
            f"{self.git.diff_vs_integration(ctx.branch)}\n\n"
            "Classify every finding as critical (correctness, security, data loss, contract breaks), major "
            "(likely production failure, missing tests for key behaviour) or minor (style, naming, nits). Only "
            "critical and major findings block. Verify that earlier findings are genuinely fixed, not just "
            "marked resolved. Approve when no critical or major issue remains.")
        res = self._invoke("michael", prompt, rctx, ReviewVerdict)
        return res.structured  # type: ignore[return-value]

    # -- validation ----------------------------------------------------------------------------------------
    def _validation_task(self, t, attempt, cancel) -> None:
        if not self.git:
            raise TaskError("validation needs the git repository (set LEGION_REPO_PATH)")
        wf = self.wf
        path = self.git.integration_worktree()
        ctx = self._ctx("eleanor", t["id"], cancel, workdir=path)
        criteria = "\n".join(f"- {x['id']} {x['title']}: " + "; ".join(x["acceptance"])
                             for x in wf.tasks() if x["kind"] == "code" and x["status"] == "done")
        res = self._invoke("eleanor", self._brief(t, (
            "Validate the integrated code (your workspace is the integration branch) against every acceptance "
            "criterion below, using run_command to execute tests and probes. Write extra tests for edge cases "
            "where useful. Report honestly.\n\nCRITERIA OF COMPLETED TASKS\n" + criteria)), ctx, ValidationReport)
        rep: ValidationReport = res.structured  # type: ignore[assignment]
        wf.record_validation("eleanor", rep.passed, rep.evidence, [d.model_dump() for d in rep.defects])
        report = f"# Validation report\n\nPassed: {rep.passed}\n\n{rep.evidence}\n\n" + "\n".join(
            f"- **{d.severity}** {d.title}: {d.description}" for d in rep.defects)
        rel = f"docs/{t['id']}.md"
        (wf.store.root / "docs").mkdir(parents=True, exist_ok=True)
        (wf.store.root / rel).write_text(report)
        wf.complete(t["id"], attempt, f"validation passed={rep.passed}", rel)
        latest = wf.latest_validation()
        if not latest["passed"]:
            self._triage(rep)

    def _triage(self, rep: ValidationReport) -> None:
        """Failures generate fix tasks assigned by Ed, then a deterministic re-validation behind them."""
        defects = "\n".join(f"- {d.severity}: {d.title} — {d.description}" for d in rep.defects)
        ids = self.plan("implementation", f"Eleanor's validation failed. Create code tasks to fix these defects, "
                                          f"each with acceptance criteria:\n{defects}")
        if not ids:
            self.wf.escalate("validation_failed", f"Validation failed and no fix tasks were planned:\n{defects}", "engine")
            return
        self.wf.add_task("Re-validate after defect fixes", "eleanor", "validation", depends_on=ids,
                         description="Re-run validation against all acceptance criteria.", created_by="engine")

    # -- planning ------------------------------------------------------------------------------------------
    def plan(self, stage: str, guidance: str = "", cancel: threading.Event | None = None) -> list[str]:
        """Ed turns the approved state into tasks for a stage. Rules are enforced by the engine on creation."""
        wf = self.wf
        ctx = ToolContext(ROSTER["ed"], wf, self.settings, None, cancel=cancel or threading.Event())
        existing = "\n".join(f"{t['id']} [{t['stage']}/{t['status']}] {t['agent']}: {t['title']}" for t in wf.tasks())
        decisions = "\n".join(f"{d['id']}: {d.body.strip()[:300]}" for d in wf.store.list("decisions"))
        docs = ""
        for t in wf.tasks():
            if t["status"] == "done" and t.get("deliverable") and t["stage"] in ("discovery", "design"):
                docs += f"\n### {t['id']} {t['title']}\n{self._deliverable(t, 5000)}\n"
        prompt = (
            f"PROJECT OBJECTIVE\n{wf.objective()}\n\nEDUCATIONAL COMPONENT: {wf.project()['educational']}\n"
            f"Plan the '{stage}' stage. Agents: {', '.join(ROSTER)}. Stages: {', '.join(STAGES)}.\n"
            "Rules: tasks must be independent where possible so they can run in parallel; make dependencies explicit; "
            "agree interfaces early by giving the interface-defining task as a dependency; code tasks (kind=code) go to "
            "teddy, carlos, radek, ralph or david, need acceptance criteria, and are reviewed by Michael; Eleanor only "
            "gets validation-stage tasks; Michael is never assigned tasks; Will only if there is an educational "
            "component; Demis only on discovery or design when his research is relevant.\n"
            f"{guidance}\n\nEXISTING TASKS\n{existing or '(none)'}\n\nDECISIONS\n{decisions or '(none)'}\n\n"
            f"DELIVERABLES SO FAR\n{docs or '(none)'}")
        res = self._invoke("ed", prompt, ctx, Plan)
        refs: dict[str, str] = {}
        created: list[str] = []
        for p in res.structured.tasks:  # type: ignore[union-attr]
            try:
                tid = wf.add_task(p.title, p.assignee, p.stage, description=p.description, kind=p.kind,
                                  depends_on=[refs.get(d, d) for d in p.depends_on], acceptance=p.acceptance,
                                  created_by="ed")
            except WorkflowError as exc:
                raise TaskError(f"Ed's plan was rejected at '{p.title}': {exc} (created so far: {created})") from exc
            refs[p.ref] = tid
            created.append(tid)
        return created


__all__ = ["Runner", "TaskError", "Cancelled", "ReviewVerdict", "ValidationReport", "Plan"]
