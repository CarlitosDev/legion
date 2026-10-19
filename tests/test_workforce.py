import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workforce import (
    GATES, add_task, approve_gate, create_project, mark_done, ready_tasks,
    open_pull_request, review_pull_request, merge_pull_request, record_validation,
    claim_task, retry_task, close_pull_request, priority_rank, sync_jira_backlog,
)


class WorkforceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "project": None, "gate_index": 0, "approvals": [], "tasks": [],
            "pull_requests": [], "validation": None, "validation_history": [],
        }
        create_project(self.state, "demo", "ship a demo")

    def test_gate_requires_explicit_evidence_and_enforces_order(self):
        with self.assertRaisesRegex(ValueError, "expected project_approval"):
            approve_gate(self.state, GATES[1], "owner", "approved")
        with self.assertRaisesRegex(ValueError, "requires a recorded"):
            approve_gate(self.state, GATES[0], "owner", " ")
        approve_gate(self.state, GATES[0], "owner", "Scope and success criteria approved.")
        self.assertEqual(self.state["project"]["status"], "planning")

    def test_dependency_scheduler_releases_downstream_task(self):
        add_task(self.state, "A", "Define contract", "hadrian", [], kind="non_code")
        add_task(self.state, "B", "Implement API", "teddy", ["A"])
        self.assertEqual(ready_tasks(self.state), [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        self.assertEqual(ready_tasks(self.state), [])
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")
        self.assertEqual([task["id"] for task in ready_tasks(self.state)], ["A"])
        with self.assertRaisesRegex(ValueError, "not ready"):
            claim_task(self.state, "B", "teddy")
        claim_task(self.state, "A", "hadrian")
        with self.assertRaisesRegex(ValueError, "not ready"):
            claim_task(self.state, "A", "hadrian")
        mark_done(self.state, "A")
        self.assertEqual([task["id"] for task in ready_tasks(self.state)], ["B"])
        claim_task(self.state, "B", "teddy")
        with self.assertRaisesRegex(ValueError, "merged PR"):
            mark_done(self.state, "B")
        open_pull_request(self.state, "PR-B", "Implement API", "B")
        review_pull_request(self.state, "PR-B", "michael", "approved", "API reviewed.")
        merge_pull_request(self.state, "PR-B")
        mark_done(self.state, "B")
        self.assertEqual(ready_tasks(self.state), [])

    def test_priority_rank_orders_jira_priorities_and_known_aliases(self):
        self.assertLess(priority_rank("Highest"), priority_rank("High"))
        self.assertLess(priority_rank("High"), priority_rank("Medium"))
        self.assertLess(priority_rank("Medium"), priority_rank("Low"))
        self.assertLess(priority_rank("Low"), priority_rank("Lowest"))
        self.assertEqual(priority_rank("Blocker"), priority_rank("Highest"))
        self.assertEqual(priority_rank("Critical"), priority_rank("High"))
        self.assertEqual(priority_rank("Major"), priority_rank("Medium"))
        self.assertEqual(priority_rank("Minor"), priority_rank("Low"))
        self.assertEqual(priority_rank("Trivial"), priority_rank("Lowest"))
        self.assertGreater(priority_rank("Unspecified"), priority_rank("Lowest"))
        self.assertGreater(priority_rank("custom priority"), priority_rank("Lowest"))

    def test_ready_tasks_prioritize_rank_stably_but_keep_dependencies_ahead(self):
        add_task(self.state, "A", "Low prerequisite", "hadrian", [], priority="Low")
        add_task(self.state, "B", "Highest dependent", "teddy", ["A"], priority="Highest")
        add_task(self.state, "C", "High first peer", "radek", [], priority="High")
        add_task(self.state, "D", "High second peer", "david", [], priority="High")
        add_task(self.state, "E", "Unspecified peer", "will", [], priority="Unspecified")
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")

        self.assertEqual([task["id"] for task in ready_tasks(self.state)], ["C", "D", "A", "E"])
        self.assertEqual(self.state["tasks"][0]["priority"], "Low")
        mark_done_pending = self.state["tasks"][0]
        mark_done_pending["status"] = "done"
        self.assertEqual([task["id"] for task in ready_tasks(self.state)], ["B", "C", "D", "E"])

    def test_jira_backlog_sync_imports_priority_and_is_idempotent(self):
        issues = [
            {
                "key": "DATA-11",
                "fields": {
                    "summary": "Fix urgent ingestion",
                    "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Keep source details"}]}]},
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": "Highest"},
                },
            },
            {"key": "DATA-12", "fields": {"summary": "Unprioritized issue", "status": {"name": "Open"}, "issuetype": {"name": "Task"}}},
        ]
        sync_jira_backlog(self.state, issues)
        self.assertEqual(len(self.state["tasks"]), 2)
        urgent = next(task for task in self.state["tasks"] if task["jira_key"] == "DATA-11")
        unspecified = next(task for task in self.state["tasks"] if task["jira_key"] == "DATA-12")
        self.assertEqual(urgent["title"], "Fix urgent ingestion")
        self.assertEqual(urgent["priority"], "Highest")
        self.assertEqual(urgent["description"], "Keep source details")
        self.assertEqual(urgent["status"], "To Do")
        self.assertEqual(urgent["issue_type"], "Bug")
        self.assertEqual(unspecified["priority"], "Unspecified")
        self.assertEqual(unspecified["description"], "")

        revised = [{"key": "DATA-11", "fields": {"summary": "Updated summary", "status": {"name": "In Progress"}, "issuetype": {"name": "Bug"}, "priority": {"name": "Critical"}}}]
        sync_jira_backlog(self.state, revised)
        self.assertEqual(len(self.state["tasks"]), 2)
        updated = next(task for task in self.state["tasks"] if task["jira_key"] == "DATA-11")
        self.assertEqual(updated["title"], "Updated summary")
        self.assertEqual(updated["priority"], "Critical")
        self.assertEqual(updated["description"], "")
        self.assertEqual(updated["status"], "In Progress")

    def test_independent_tasks_can_be_claimed_once_by_the_assigned_agents(self):
        add_task(self.state, "A", "Build data path", "radek", [])
        add_task(self.state, "B", "Build API", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")
        claim_task(self.state, "A", "radek")
        claim_task(self.state, "B", "teddy")
        with self.assertRaisesRegex(ValueError, "assigned agent"):
            claim_task(self.state, "A", "teddy")
        self.assertEqual(ready_tasks(self.state), [])

    def test_interrupted_work_can_be_retried_with_an_audit_reason(self):
        add_task(self.state, "A", "Implement work", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")
        claim_task(self.state, "A", "teddy")
        retry_task(self.state, "A", "Worker interrupted", "carlos")
        self.assertEqual(ready_tasks(self.state)[0]["agent"], "carlos")
        self.assertEqual(self.state["tasks"][0]["retry_history"][0]["reason"], "Worker interrupted")

    def test_retry_requires_fresh_pr_review_for_the_new_attempt(self):
        add_task(self.state, "A", "Implement feature", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Design approved.")
        claim_task(self.state, "A", "teddy")
        open_pull_request(self.state, "PR-1", "First implementation", "A")
        review_pull_request(self.state, "PR-1", "michael", "approved", "First attempt reviewed.")
        merge_pull_request(self.state, "PR-1")
        retry_task(self.state, "A", "Additional work required.", "carlos")
        claim_task(self.state, "A", "carlos")
        with self.assertRaisesRegex(ValueError, "current attempt"):
            mark_done(self.state, "A")
        open_pull_request(self.state, "PR-2", "Follow-up changes", "A")
        review_pull_request(self.state, "PR-2", "michael", "approved", "Follow-up reviewed.")
        merge_pull_request(self.state, "PR-2")
        mark_done(self.state, "A")

    def test_task_retry_requires_open_pr_to_be_closed_first(self):
        add_task(self.state, "A", "Implement feature", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Design approved.")
        claim_task(self.state, "A", "teddy")
        open_pull_request(self.state, "PR-1", "Implementation", "A")
        with self.assertRaisesRegex(ValueError, "open pull request"):
            retry_task(self.state, "A", "Interrupted")
        close_pull_request(self.state, "PR-1", "Superseded by retry.")
        retry_task(self.state, "A", "Interrupted")
        self.assertEqual(self.state["tasks"][0]["status"], "ready")

    def test_preproduction_requires_at_least_one_completed_task(self):
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")
        with self.assertRaisesRegex(ValueError, "requires planned tasks"):
            approve_gate(self.state, GATES[2], "owner", "Testing complete.")

    def test_only_michael_can_approve_a_pull_request_for_merge(self):
        add_task(self.state, "T1", "Implement feature", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")
        claim_task(self.state, "T1", "teddy")
        open_pull_request(self.state, "PR-1", "Feature implementation", "T1")
        with self.assertRaisesRegex(ValueError, "Michael"):
            merge_pull_request(self.state, "PR-1")
        with self.assertRaisesRegex(ValueError, "Michael"):
            review_pull_request(self.state, "PR-1", "teddy", "approved", "Looks good.")
        review_pull_request(self.state, "PR-1", "michael", "changes_requested", "Handle timeout.")
        with self.assertRaisesRegex(ValueError, "Michael approves"):
            merge_pull_request(self.state, "PR-1")
        review_pull_request(self.state, "PR-1", "michael", "approved", "Timeout handling verified.")
        merge_pull_request(self.state, "PR-1")
        self.assertEqual(self.state["pull_requests"][0]["status"], "merged")

    def test_preproduction_requires_eleanor_pass_and_merged_prs(self):
        add_task(self.state, "T1", "Implement feature", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Design approved.")
        claim_task(self.state, "T1", "teddy")
        open_pull_request(self.state, "PR-1", "Feature", "T1")
        review_pull_request(self.state, "PR-1", "michael", "approved", "Reviewed.")
        with self.assertRaisesRegex(ValueError, "merged PR"):
            mark_done(self.state, "T1")
        merge_pull_request(self.state, "PR-1")
        mark_done(self.state, "T1")
        with self.assertRaisesRegex(ValueError, "Eleanor"):
            approve_gate(self.state, GATES[2], "owner", "Tests passed.")
        with self.assertRaisesRegex(ValueError, "Eleanor"):
            record_validation(self.state, "teddy", True, "All checks passed.")
        record_validation(self.state, "eleanor", False, "Acceptance test failed.")
        with self.assertRaisesRegex(ValueError, "Eleanor"):
            approve_gate(self.state, GATES[2], "owner", "Tests passed.")
        record_validation(self.state, "eleanor", True, "Acceptance criteria all passed.")
        approve_gate(self.state, GATES[2], "owner", "Test report reviewed.")
        self.assertEqual(self.state["project"]["status"], "preproduction_approved")

    def test_code_task_cannot_be_marked_done_without_a_merged_pr(self):
        add_task(self.state, "T1", "Implement feature", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Design approved.")
        claim_task(self.state, "T1", "teddy")
        with self.assertRaisesRegex(ValueError, "merged PR"):
            mark_done(self.state, "T1")

    def test_scope_is_frozen_after_preproduction_approval(self):
        add_task(self.state, "T1", "Implement feature", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Design approved.")
        claim_task(self.state, "T1", "teddy")
        open_pull_request(self.state, "PR-1", "Feature", "T1")
        review_pull_request(self.state, "PR-1", "michael", "approved", "Reviewed.")
        merge_pull_request(self.state, "PR-1")
        mark_done(self.state, "T1")
        record_validation(self.state, "eleanor", True, "Acceptance suite passed.")
        approve_gate(self.state, GATES[2], "owner", "QA report accepted.")
        with self.assertRaisesRegex(ValueError, "frozen"):
            add_task(self.state, "T2", "Late scope", "teddy", [])
        with self.assertRaisesRegex(ValueError, "frozen"):
            open_pull_request(self.state, "PR-2", "Late change", "T1")
        with self.assertRaisesRegex(ValueError, "pre-production"):
            record_validation(self.state, "eleanor", False, "Late run.")
        approve_gate(self.state, GATES[3], "owner", "Production authorised.")
        self.assertEqual(self.state["project"]["status"], "deployment_approved")

    def test_tasks_cannot_be_added_without_a_project(self):
        empty = {
            "project": None, "gate_index": 0, "approvals": [], "tasks": [],
            "pull_requests": [], "validation": None, "validation_history": [],
        }
        with self.assertRaisesRegex(ValueError, "Create a project"):
            add_task(empty, "A", "Define contract", "hadrian", [])

    def test_all_four_gates_required_in_sequence(self):
        add_task(self.state, "A", "Implement work", "teddy", [])
        approve_gate(self.state, GATES[0], "owner", "Scope approved.")
        approve_gate(self.state, GATES[1], "owner", "Architecture approved.")
        with self.assertRaisesRegex(ValueError, "planned tasks"):
            approve_gate(self.state, GATES[2], "owner", "Tests passed.")
        claim_task(self.state, "A", "teddy")
        open_pull_request(self.state, "PR-1", "Work", "A")
        review_pull_request(self.state, "PR-1", "michael", "approved", "Reviewed.")
        merge_pull_request(self.state, "PR-1")
        mark_done(self.state, "A")
        record_validation(self.state, "eleanor", True, "Acceptance criteria passed.")
        approve_gate(self.state, GATES[2], "owner", "Implementation and tests reviewed.")
        approve_gate(self.state, GATES[3], "owner", "Production deployment authorised.")
        self.assertEqual(self.state["gate_index"], 4)
        self.assertEqual(len(self.state["approvals"]), 4)
        with self.assertRaisesRegex(ValueError, "expected all gates complete"):
            approve_gate(self.state, GATES[-1], "owner", "again")


if __name__ == "__main__":
    unittest.main()
