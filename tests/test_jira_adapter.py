import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jira_adapter import JiraClient, JiraConfigurationError, JiraError, main


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class JiraAdapterTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "JIRA_EMAIL": "person@example.com",
            "JIRA_API_TOKEN": "test-secret-token",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = JiraClient("https://efcloud.atlassian.net", "DATA")

    @patch("jira_adapter.urlopen")
    def test_search_uses_project_scoped_jql_and_returns_issues(self, urlopen):
        urlopen.return_value = FakeResponse({"issues": [{"key": "DATA-1"}], "isLast": True})
        self.assertEqual(self.client.search_issues(), [{"key": "DATA-1"}])
        request = urlopen.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)["jql"],
                         ["project = DATA ORDER BY created DESC"])
        self.assertIn("priority", parse_qs(urlsplit(request.full_url).query)["fields"][0].split(","))
        self.assertTrue(urlsplit(request.full_url).path.endswith("/search/jql"))
        self.assertEqual(request.get_header("Authorization"), "Basic " + base64.b64encode(
            b"person@example.com:test-secret-token").decode())

    @patch("jira_adapter.urlopen")
    def test_create_task_posts_minimal_task_fields(self, urlopen):
        urlopen.return_value = FakeResponse({"key": "DATA-2", "id": "42"})
        result = self.client.create_task("Review data model", "Check the proposed schema")
        self.assertEqual(result["key"], "DATA-2")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"fields": {
            "project": {"key": "DATA"}, "issuetype": {"name": "Task"},
            "summary": "Review data model", "description": {
                "type": "doc", "version": 1, "content": [{"type": "paragraph",
                    "content": [{"type": "text", "text": "Check the proposed schema"}]}]},
        }})

    @patch("jira_adapter.urlopen")
    def test_create_task_includes_priority_only_when_requested(self, urlopen):
        urlopen.return_value = FakeResponse({"key": "DATA-3", "id": "43"})
        self.client.create_task("Urgent work", priority="Highest")
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)["fields"]["priority"], {"name": "Highest"})

        urlopen.return_value = FakeResponse({"key": "DATA-4", "id": "44"})
        self.client.create_task("Default priority work")
        request = urlopen.call_args.args[0]
        self.assertNotIn("priority", json.loads(request.data)["fields"])

    def test_rejects_non_jira_host_before_network_access(self):
        with self.assertRaises(JiraConfigurationError):
            JiraClient("https://example.org", "DATA")
        with self.assertRaises(JiraConfigurationError):
            JiraClient("https://efcloud.atlassian.net.evil.example", "DATA")

    @patch("jira_adapter.urlopen")
    def test_search_follows_pagination_tokens_until_requested_limit(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"issues": [{"key": "DATA-1"}], "nextPageToken": "next", "isLast": False}),
            FakeResponse({"issues": [{"key": "DATA-2"}], "isLast": True}),
        ]
        self.assertEqual([issue["key"] for issue in self.client.search_issues(limit=2)],
                         ["DATA-1", "DATA-2"])
        second_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(parse_qs(urlsplit(second_request.full_url).query)["nextPageToken"], ["next"])

    def test_requires_credentials_without_exposing_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(JiraConfigurationError) as error:
                JiraClient("https://efcloud.atlassian.net", "DATA")
        self.assertNotIn("test-secret-token", str(error.exception))

    @patch("jira_adapter.urlopen")
    def test_http_errors_are_reported_without_response_body(self, urlopen):
        from urllib.error import HTTPError
        urlopen.side_effect = HTTPError("url", 403, "Forbidden", {}, None)
        with self.assertRaisesRegex(JiraError, "HTTP 403") as error:
            self.client.search_issues()
        self.assertNotIn("test-secret-token", str(error.exception))

    @patch("jira_adapter.urlopen")
    def test_cli_blocks_remote_creation_without_gate_one(self, urlopen):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "jira.json"
            config.write_text(json.dumps({"base_url": "https://efcloud.atlassian.net",
                                          "project_key": "DATA", "issue_type": "Task"}))
            state = root / "state.json"
            state.write_text(json.dumps({"project": {"name": "Legion"}, "gate_index": 0}))
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                result = main(["--config", str(config), "--state", str(state), "create",
                               "--summary", "Never sent"])
        self.assertEqual(result, 2)
        self.assertIn("Gate 1", output.getvalue())
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
