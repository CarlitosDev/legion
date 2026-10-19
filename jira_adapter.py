#!/usr/bin/env python3
"""Minimal Jira Cloud adapter for listing and creating Legion project tasks."""
import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class JiraConfigurationError(ValueError):
    """Raised when the site, project, or credentials are invalid or missing."""


class JiraError(RuntimeError):
    """Raised for Jira transport or API errors without exposing response bodies."""


def _description_document(text):
    """Convert plain text to the Atlassian Document Format used by REST v3."""
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines() if line
    ]
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": paragraphs}


class JiraClient:
    def __init__(self, base_url, project_key, issue_type="Task", timeout=20):
        parsed = urlsplit(base_url)
        try:
            valid_port = parsed.port in (None, 443)
        except ValueError:
            valid_port = False
        if (parsed.scheme != "https" or not parsed.hostname
                or not (parsed.hostname == "atlassian.net"
                        or parsed.hostname.endswith(".atlassian.net"))
                or not valid_port or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise JiraConfigurationError("Jira site must be an HTTPS *.atlassian.net URL.")
        if not re.fullmatch(r"[A-Z][A-Z0-9]+", project_key):
            raise JiraConfigurationError("Jira project key must contain uppercase letters and digits.")
        if not issue_type.strip():
            raise JiraConfigurationError("Jira issue type is required.")
        self.base_url = "https://" + parsed.hostname
        self.project_key = project_key
        self.issue_type = issue_type
        self.timeout = timeout
        email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
        if not email or not token or "\r" in email or "\n" in email or "\r" in token or "\n" in token:
            raise JiraConfigurationError(
                "Set valid JIRA_EMAIL and JIRA_API_TOKEN values in the environment to connect to Jira."
            )
        encoded = base64.b64encode((email + ":" + token).encode()).decode()
        self.authorization = "Basic " + encoded

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(self.base_url + path, data=body, method=method)
        request.add_header("Authorization", self.authorization)
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read()
                return json.loads(data.decode()) if data else {}
        except HTTPError as exc:
            raise JiraError("Jira returned HTTP {}.".format(exc.code)) from None
        except URLError as exc:
            reason = getattr(exc, "reason", "connection failed")
            raise JiraError("Could not reach Jira: {}.".format(reason)) from None
        except (ValueError, UnicodeDecodeError):
            raise JiraError("Jira returned an invalid JSON response.") from None

    def search_issues(self, jql=None, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError("Issue limit must be between 1 and 100.")
        query = jql or "project = {} ORDER BY created DESC".format(self.project_key)
        issues, page_token = [], None
        while len(issues) < limit:
            page_size = min(50, limit - len(issues))
            params = {
                "jql": query, "maxResults": page_size,
                "fields": "summary,status,issuetype,priority,assignee,labels",
            }
            if page_token:
                params["nextPageToken"] = page_token
            result = self._request("GET", "/rest/api/3/search/jql?" + urlencode(params))
            issues.extend(result.get("issues", []))
            page_token = result.get("nextPageToken")
            if result.get("isLast", True) or not page_token or not result.get("issues"):
                break
        return issues[:limit]

    def create_task(self, summary, description=""):
        if not summary.strip():
            raise ValueError("Jira task summary is required.")
        fields = {
            "project": {"key": self.project_key},
            "issuetype": {"name": self.issue_type},
            "summary": summary.strip(),
        }
        if description.strip():
            fields["description"] = _description_document(description)
        return self._request("POST", "/rest/api/3/issue", {"fields": fields})


def _client(config_path):
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError) as exc:
        raise JiraConfigurationError("Could not read Jira config at {}.".format(config_path)) from exc
    if not isinstance(config, dict):
        raise JiraConfigurationError("Jira config must be a JSON object.")
    return JiraClient(config.get("base_url", ""), config.get("project_key", ""),
                      config.get("issue_type", "Task"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).parent / ".codex" / "jira.json")
    parser.add_argument("--state", type=Path, default=Path(".workforce/state.json"),
                        help="Local workflow state; task creation requires Gate 1 approval")
    commands = parser.add_subparsers(dest="command", required=True)
    list_command = commands.add_parser("list", help="List recent project issues")
    list_command.add_argument("--jql")
    list_command.add_argument("--limit", type=int, default=50)
    create_command = commands.add_parser("create", help="Create a task in the configured project")
    create_command.add_argument("--summary", required=True)
    create_command.add_argument("--description", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            try:
                state = json.loads(args.state.read_text())
            except (OSError, ValueError):
                raise JiraConfigurationError(
                    "Create a local workforce project and record Gate 1 approval before creating Jira tasks."
                ) from None
            if not state.get("project") or state.get("gate_index", 0) < 1:
                raise JiraConfigurationError(
                    "Jira task creation requires a local project with Gate 1 approval."
                )
        client = _client(args.config)
        if args.command == "list":
            result = client.search_issues(args.jql, args.limit)
        else:
            result = client.create_task(args.summary, args.description)
        print(json.dumps(result, indent=2))
    except (JiraConfigurationError, JiraError, ValueError) as exc:
        print("Jira adapter error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
