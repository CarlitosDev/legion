import pytest

from conftest import drive_to_implementation
from legion.engine import WorkflowError


def test_gates_are_ordered_and_need_evidence(wf):
    with pytest.raises(WorkflowError, match="expected project_approval"):
        wf.approve_gate("technical_design_approval", "owner", "x")
    with pytest.raises(WorkflowError, match="named approver"):
        wf.approve_gate("project_approval", "owner", " ")


def test_tasks_wait_for_their_gate(wf):
    d = wf.add_task("Proposal", "renato", "discovery")
    g = wf.add_task("Design", "hadrian", "design", depends_on=[d])
    assert [t["id"] for t in wf.runnable()] == [d]
    a = wf.claim(d, "renato"); wf.complete(d, a, "ok", "x")
    assert wf.runnable() == []                       # design needs gate 1
    wf.approve_gate("project_approval", "owner", "ok")
    assert [t["id"] for t in wf.runnable()] == [g]


def test_gate_blocked_by_unfinished_stage_tasks(wf):
    wf.add_task("Proposal", "renato", "discovery")
    with pytest.raises(WorkflowError, match="not complete"):
        wf.approve_gate("project_approval", "owner", "ok")


def test_code_task_needs_merged_approved_pr(wf):
    c = drive_to_implementation(wf)
    attempt = wf.claim(c, "teddy")
    with pytest.raises(WorkflowError, match="merged"):
        wf.complete(c, attempt, "done")
    pr = wf.open_pr(c, "legion/T3", "API", "teddy")
    with pytest.raises(WorkflowError, match="no Michael approval"):
        wf.authorize_merge(pr, True)
    assert wf.review_pr(pr, "michael", "approved", "lgtm") == "approved"
    with pytest.raises(WorkflowError, match="checks"):
        wf.authorize_merge(pr, False)
    wf.mark_merged(pr)
    wf.complete(c, attempt, "done")
    assert wf.task(c)["status"] == "done"


def test_only_michael_reviews_and_blocking_findings_downgrade_approval(wf):
    c = drive_to_implementation(wf)
    wf.claim(c, "teddy")
    pr = wf.open_pr(c, "b", "t", "teddy")
    with pytest.raises(WorkflowError, match="Only Michael"):
        wf.review_pr(pr, "teddy", "approved", "self")
    eff = wf.review_pr(pr, "michael", "approved", "hmm",
                       [{"severity": "critical", "text": "race condition"}])
    assert eff == "changes_requested"
    wf.resolve_finding(pr, 1, "teddy", "added lock")
    assert wf.review_pr(pr, "michael", "approved", "ok now",
                        [{"severity": "minor", "text": "naming"}]) == "approved"


def test_minor_findings_never_block(wf):
    c = drive_to_implementation(wf)
    wf.claim(c, "teddy")
    pr = wf.open_pr(c, "b", "t", "teddy")
    assert wf.review_pr(pr, "michael", "approved", "nits",
                        [{"severity": "minor", "text": "style"}]) == "approved"


def test_review_deadlock_escalates_instead_of_blocking_forever(wf):
    c = drive_to_implementation(wf)
    wf.claim(c, "teddy")
    pr = wf.open_pr(c, "b", "t", "teddy")
    for _ in range(2):
        wf.review_pr(pr, "michael", "changes_requested", "no", [{"severity": "major", "text": "x"}])
    assert wf.store.read("prs", pr)["status"] == "escalated"
    assert [e["kind"] for e in wf.store.list("escalations")] == ["review_deadlock"]


def test_failed_attempts_retry_then_escalate(wf):
    c = drive_to_implementation(wf)
    a = wf.claim(c, "teddy"); wf.fail(c, a, "timeout")
    assert wf.task(c)["status"] == "ready"
    a = wf.claim(c, "teddy"); wf.fail(c, a, "timeout")
    assert wf.task(c)["status"] == "failed"
    assert wf.runnable() == []
    eid = wf.store.list("escalations")[0]["id"]
    with pytest.raises(WorkflowError, match="escalations"):
        wf.approve_gate("preproduction_approval", "owner", "x")
    wf.resolve_escalation(eid, "owner", "reset it")
    wf.retry(c, "owner", "fixed env")
    assert wf.task(c)["status"] == "ready"


def test_stale_attempt_cannot_complete(wf):
    d = wf.add_task("Proposal", "renato", "discovery")
    a = wf.claim(d, "renato")
    wf.recover_interrupted()
    with pytest.raises(WorkflowError, match="no longer the active attempt"):
        wf.complete(d, a, "late", "x")


def test_recover_interrupted_requeues(wf):
    d = wf.add_task("Proposal", "renato", "discovery")
    wf.claim(d, "renato")
    assert wf.recover_interrupted() == [d]
    assert wf.task(d)["status"] == "ready"


def test_preproduction_needs_fresh_passing_validation(wf):
    c = drive_to_implementation(wf)
    a = wf.claim(c, "teddy")
    pr = wf.open_pr(c, "b", "t", "teddy")
    wf.review_pr(pr, "michael", "approved", "ok"); wf.mark_merged(pr); wf.complete(c, a, "ok")
    with pytest.raises(WorkflowError, match="validation"):
        wf.approve_gate("preproduction_approval", "owner", "x")
    wf.record_validation("eleanor", True, "suite green", [{"severity": "critical", "title": "x"}])
    with pytest.raises(WorkflowError, match="passing validation"):   # critical defect forces fail
        wf.approve_gate("preproduction_approval", "owner", "x")
    wf.record_validation("eleanor", True, "suite green")
    wf.approve_gate("preproduction_approval", "owner", "accepted")
    with pytest.raises(WorkflowError, match="frozen"):
        wf.add_task("More", "teddy", "implementation", kind="code", acceptance=["x"])


def test_will_only_for_educational_projects(wf):
    with pytest.raises(WorkflowError, match="educational"):
        wf.add_task("Pedagogy review", "will", "discovery")


def test_baseline_decisions_need_evidence_to_reopen(wf):
    drive_to_implementation(wf)
    did = wf.record_decision("hadrian", "Use DynamoDB", task_id="T-2")
    wf.freeze_baseline()
    with pytest.raises(WorkflowError, match="Insufficient"):
        wf.challenge_decision(did, "teddy", "I prefer Postgres")
    eid = wf.challenge_decision(did, "teddy", "Load test shows 40ms p99 over budget at 2k rps; see run 17 logs.")
    assert wf.store.read("escalations", eid)["kind"] == "architecture_change"
    with pytest.raises(WorkflowError, match="already under challenge"):
        wf.challenge_decision(did, "teddy", "Load test shows 40ms p99 over budget at 2k rps; see run 17 logs.")


def test_role_rules(wf):
    with pytest.raises(WorkflowError, match="Michael"):
        wf.add_task("x", "michael", "implementation")
    with pytest.raises(WorkflowError, match="cannot own code"):
        wf.add_task("x", "tim", "implementation", kind="code", acceptance=["a"])
    with pytest.raises(WorkflowError, match="Validation tasks belong to Eleanor"):
        wf.add_task("x", "teddy", "validation")
