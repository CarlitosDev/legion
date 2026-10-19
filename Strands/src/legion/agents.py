"""Strands agent construction and the invoker abstraction the runner depends on."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .roster import ROSTER
from .settings import Settings
from .tools import ToolContext, build_tools

T = TypeVar("T", bound=BaseModel)


@dataclass
class InvokeResult:
    text: str
    structured: BaseModel | None = None
    tokens: int = 0
    stop_reason: str = ""


class Invoker(Protocol):
    def invoke(self, agent_id: str, prompt: str, ctx: ToolContext,
               structured: type[T] | None = None) -> InvokeResult: ...


def make_model(settings: Settings, agent_id: str):
    """One Bedrock model per agent; model id and reasoning budget are configurable per agent."""
    import boto3
    from strands.models import BedrockModel

    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    budget = settings.agent_thinking_budget.get(agent_id)
    cfg: dict = {"model_id": settings.model_id_for(agent_id), "max_tokens": settings.max_output_tokens}
    if budget:
        cfg["max_tokens"] = max(settings.max_output_tokens, budget + 4096)
        cfg["additional_request_fields"] = {"thinking": {"type": "enabled", "budget_tokens": budget}}
    else:
        cfg["temperature"] = settings.temperature  # extended thinking requires default temperature
    return BedrockModel(boto_session=session, **cfg)


class StrandsInvoker:
    """Builds a fresh Strands Agent per invocation. Durable state lives in the markdown ledger, not in
    conversation history, so any task can be resumed after a crash."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._models: dict[str, object] = {}

    def invoke(self, agent_id, prompt, ctx, structured=None):
        from strands import Agent

        spec = ROSTER[agent_id]
        model = self._models.get(agent_id) or self._models.setdefault(agent_id, make_model(self.settings, agent_id))
        agent = Agent(model=model, system_prompt=spec.system_prompt(), tools=build_tools(ctx),
                      callback_handler=None, name=spec.name, agent_id=spec.id)
        kwargs = {"cancel_signal": ctx.cancel, "limits": {"total_tokens": self.settings.max_task_tokens}}
        if structured is not None:
            kwargs["structured_output_model"] = structured
        result = agent(prompt, **kwargs)
        usage = getattr(result.metrics, "accumulated_usage", {}) or {}
        return InvokeResult(text=str(result), structured=result.structured_output,
                            tokens=int(usage.get("totalTokens", 0)), stop_reason=str(result.stop_reason))
