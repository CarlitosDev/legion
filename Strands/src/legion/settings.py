"""Runtime configuration. Everything is overridable via LEGION_* env vars or .env."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .roster import ROSTER


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEGION_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    # AWS / Bedrock
    aws_profile: str | None = None
    aws_region: str = "eu-west-1"
    # alias -> Bedrock model id or inference-profile id. Luna's id must be supplied by the user.
    model_catalog: dict[str, str] = Field(default_factory=lambda: {
        "opus": "eu.anthropic.claude-opus-4-5-20251101-v1:0",
        "luna": "",
    })
    default_model: str = "opus"
    # agent id -> catalog alias (or raw model id). Missing agents use default_model.
    agent_models: dict[str, str] = Field(default_factory=dict)
    max_output_tokens: int = 8192
    # agent id -> extended-thinking token budget (omit for none). Demis gets the strongest reasoning by default.
    agent_thinking_budget: dict[str, int] = Field(default_factory=lambda: {"demis": 8000, "hadrian": 4000})
    temperature: float = 0.3

    # Concurrency, time and budget
    max_parallel_agents: int = 3
    task_timeout_s: int = 300
    agent_timeouts: dict[str, int] = Field(default_factory=lambda: {"demis": 600})
    max_task_attempts: int = 3
    max_review_rounds: int = 3
    max_project_tokens: int = 5_000_000
    max_task_tokens: int = 400_000

    # Local state (markdown) and code
    state_dir: Path = Path(".legion")
    repo_path: Path | None = None
    base_branch: str = "main"
    integration_branch: str = "legion/integration"  # task PRs merge here; release PR merges it to base_branch
    check_command: str = "python -m pytest -q"      # automated checks run in each task worktree before merge
    check_timeout_s: int = 600
    shell_timeout_s: int = 120                      # per agent shell command

    # Integrations (credentials are read from the environment, never stored)
    github_repo: str = "CarlitosDev/legion"
    jira_base_url: str = "https://efcloud.atlassian.net"
    jira_project: str = "DATA"
    jira_enabled: bool = False
    github_enabled: bool = False
    jira_issue_type: str = "Task"
    # workflow status -> Jira transition name
    jira_status_map: dict[str, str] = Field(default_factory=lambda: {
        "ready": "To Do", "in_progress": "In Progress", "done": "Done"})

    def timeout_for(self, agent_id: str) -> int:
        return self.agent_timeouts.get(agent_id, self.task_timeout_s)

    def model_id_for(self, agent_id: str) -> str:
        if agent_id not in ROSTER:
            raise KeyError(f"Unknown agent: {agent_id}")
        alias = self.agent_models.get(agent_id) or ROSTER[agent_id].default_model or self.default_model
        model_id = self.model_catalog.get(alias, alias)
        if not model_id:
            raise ValueError(
                f"No Bedrock model id configured for alias '{alias}' (agent {agent_id}). "
                f"Set LEGION_MODEL_CATALOG__{alias.upper()}."
            )
        return model_id
