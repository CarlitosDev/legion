import time


from conftest import FakeInvoker, drive_to_implementation, make_stack
from legion.runner import Defect, Finding, Plan, PlannedTask, ReviewVerdict, ValidationReport


def planner(*tasks):
    return lambda p, c, s: Plan(tasks=list(tasks))


def test_full_lifecycle_with_review_loop_and_merge(wf, settings, repo):
    reviews = iter([
        ReviewVerdict(verdict="changes_requested", summary="needs a guard",
                      findings=[Finding(severity="major", text="no input validation")]),
        ReviewVerdict(verdict="approved", summary="good", findings=[Finding(severity="minor", text="naming")]),
    ])
    rounds = {"n": 0}

    def teddy(prompt, ctx, _):
        rounds["n"] += 1
        (ctx.workdir / f"api_{rounds['n']}.py").write_text("print('ok')\n")
        if "requested changes" in prompt:
            ctx.workflow.resolve_finding("PR-1", 1, "teddy", "added validation")
        return "done"

    inv = FakeInvoker(
        renato=lambda *a: "# Proposal\nBuild it.",
        hadrian=lambda *a: "# Architecture\nA monolith.",
        ed=planner(PlannedTask(ref="a", title="Build API", assignee="teddy", stage="implementation", kind="code",
                               acceptance=["returns 200"])),
        teddy=teddy, michael=lambda p, c, s: next(reviews),
        eleanor=lambda p, c, s: ValidationReport(passed=True, evidence="all green"))
    runner, orch, git = make_stack(wf, settings, repo, inv)

    wf.add_task("Proposal", "renato", "discovery")
    orch.run()
    wf.approve_gate("project_approval", "owner", "scope ok")
    wf.add_task("Architecture", "hadrian", "design")
    orch.run()
    assert runner.plan("implementation") == ["T-3"]
    wf.approve_gate("technical_design_approval", "owner", "arch ok")
    orch.run()

    t = wf.task("T-3")
    assert t["status"] == "done"
    pr = wf.store.read("prs", "PR-1")
    assert pr["status"] == "merged" and pr["round"] == 2
    assert [f["resolved"] for f in pr["findings"] if f["severity"] == "major"] == [True]
    assert (git.integration_worktree() / "api_2.py").exists()           # merged into integration, not main
    assert not (repo / "api_2.py").exists()
    assert inv.calls.count(("michael", "ReviewVerdict")) == 2

    wf.add_task("Validate", "eleanor", "validation", depends_on=["T-3"])
    orch.run()
    wf.approve_gate("preproduction_approval", "owner", "accepted")
    assert wf.status()["tokens_used"] > 0
    assert "approved gate" in (wf.store.root / "audit.md").read_text()


def test_parallelism_is_capped(wf, settings, repo):
    settings.max_parallel_agents = 3

    def slow(*a):
        time.sleep(0.15)
        return "doc"

    inv = FakeInvoker(renato=slow)
    _, orch, _ = make_stack(wf, settings, repo, inv)
    for i in range(7):
        wf.add_task(f"Research {i}", "renato", "discovery")
    summary = orch.run()
    assert len(summary.completed) == 7
    assert inv.peak == 3


def test_timeout_cancels_retries_and_escalates(wf, settings, repo):
    settings.agent_timeouts = {"renato": 0.3}
    settings.max_task_attempts = 2

    def hang(prompt, ctx, _):
        ctx.cancel.wait(5)           # a well-behaved agent loop stops when the signal fires
        ctx.check()

    inv = FakeInvoker(renato=hang)
    _, orch, _ = make_stack(wf, settings, repo, inv)
    wf.add_task("Research", "renato", "discovery")
    summary = orch.run()
    assert summary.failed == ["T-1", "T-1"]
    t = wf.task("T-1")
    assert t["status"] == "failed" and "timed out" in t.body
    assert [e["kind"] for e in wf.store.list("escalations")] == ["task_failed"]


def test_demis_gets_a_longer_budget_by_default(settings):
    assert settings.timeout_for("demis") == 600 and settings.timeout_for("tim") == 300


def test_interrupted_run_resumes(wf, settings, repo):
    inv = FakeInvoker(renato=lambda *a: "doc")
    _, orch, _ = make_stack(wf, settings, repo, inv)
    wf.add_task("Research", "renato", "discovery")
    wf.claim("T-1", "renato")               # simulate a crash mid-task: left in_progress
    summary = orch.run()
    assert summary.recovered == ["T-1"] and summary.completed == ["T-1"]


def test_failed_validation_creates_fix_tasks_and_revalidation(wf, settings, repo):
    c = drive_to_implementation(wf)
    inv = FakeInvoker(
        teddy=lambda p, ctx, _: (ctx.workdir / "x.py").write_text("1\n") or "ok",
        michael=lambda p, c, s: ReviewVerdict(verdict="approved", summary="ok"),
        eleanor=lambda p, c, s: ValidationReport(passed=False, evidence="login 500s", defects=[
            Defect(title="Login crashes", severity="critical", description="500 on empty body")]),
        ed=planner(PlannedTask(ref="f", title="Fix login", assignee="teddy", stage="implementation", kind="code",
                               acceptance=["empty body returns 400"])))
    _, orch, _ = make_stack(wf, settings, repo, inv)
    orch.run()
    wf.add_task("Validate", "eleanor", "validation", depends_on=[c])
    orch.run(max_tasks=1)
    titles = [t["title"] for t in wf.tasks()]
    assert "Fix login" in titles and "Re-validate after defect fixes" in titles
    reval = next(t for t in wf.tasks() if t["title"].startswith("Re-validate"))
    assert reval["depends_on"] == [next(t["id"] for t in wf.tasks() if t["title"] == "Fix login")]
    assert wf.latest_validation()["passed"] is False


def test_budget_stop_escalates_and_blocks(wf, settings, repo):
    settings.max_project_tokens = 50
    inv = FakeInvoker(renato=lambda *a: "doc")
    _, orch, _ = make_stack(wf, settings, repo, inv)
    for i in range(4):
        wf.add_task(f"R{i}", "renato", "discovery")
    summary = orch.run()
    assert summary.stopped_for_budget and len(summary.completed) < 4
    assert [e["kind"] for e in wf.store.list("escalations")] == ["budget"]
