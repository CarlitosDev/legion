"""Drives a real strands.Agent (event loop, tool dispatch, structured output) with a scripted model: no network."""
import json

import pytest
from strands.models.model import Model

from legion.agents import StrandsInvoker
from legion.roster import ROSTER
from legion.runner import ReviewVerdict
from legion.tools import ToolContext


class ScriptedModel(Model):
    """Each script entry is one assistant turn: a list of ("text", s) or ("tool", name, input)."""

    def __init__(self, *turns):
        self.turns, self.i, self.seen_tools = list(turns), 0, []

    def update_config(self, **kw): ...
    def get_config(self): return {"model_id": "scripted"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kw):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.seen_tools = [t["name"] for t in tool_specs or []]
        turn = self.turns[min(self.i, len(self.turns) - 1)]
        self.i += 1
        yield {"messageStart": {"role": "assistant"}}
        uses_tool = False
        for n, item in enumerate(turn):
            if item[0] == "text":
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": item[1]}}}
            else:
                uses_tool = True
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{self.i}-{n}", "name": item[1]}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(item[2])}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if uses_tool else "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                            "metrics": {"latencyMs": 1}}}


def ctx_for(agent_id, wf, settings, tmp_path, **kw):
    return ToolContext(ROSTER[agent_id], wf, settings, task_id=None, workdir=tmp_path, **kw)


def test_agent_uses_confined_tools_and_reports_tokens(wf, settings, tmp_path):
    inv = StrandsInvoker(settings)
    inv._models["teddy"] = ScriptedModel(
        [("tool", "write_file", {"path": "svc/app.py", "content": "print(1)\n"}),
         ("tool", "write_file", {"path": "../escape.py", "content": "evil"}),
         ("tool", "run_command", {"command": "git push origin main"}),
         ("tool", "record_decision", {"text": "Use FastAPI"})],
        [("text", "implemented")])
    res = inv.invoke("teddy", "build it", ctx_for("teddy", wf, settings, tmp_path))
    assert res.text.strip() == "implemented"
    assert (tmp_path / "svc/app.py").read_text() == "print(1)\n"
    assert not (tmp_path.parent / "escape.py").exists()                     # path confinement
    assert wf.store.list("decisions")[0].body.strip() == "Use FastAPI"
    assert res.tokens == 30                                                  # two model turns
    model = inv._models["teddy"]
    assert "approve_gate" not in model.seen_tools and "write_file" in model.seen_tools


def test_structured_output_returns_pydantic_model(wf, settings, tmp_path):
    inv = StrandsInvoker(settings)
    inv._models["michael"] = ScriptedModel(
        [("tool", "ReviewVerdict", {"verdict": "changes_requested", "summary": "race",
                                    "findings": [{"severity": "critical", "text": "unlocked counter"}]})],
        [("text", "done")])
    res = inv.invoke("michael", "review", ctx_for("michael", wf, settings, tmp_path), ReviewVerdict)
    assert isinstance(res.structured, ReviewVerdict) and res.structured.findings[0].severity == "critical"


def test_reviewer_has_no_write_or_shell_tools(wf, settings, tmp_path):
    inv = StrandsInvoker(settings)
    m = inv._models["michael"] = ScriptedModel([("text", "ok")])
    inv.invoke("michael", "hi", ctx_for("michael", wf, settings, tmp_path))
    assert set(m.seen_tools) == {"list_files", "read_file"}


def test_invocation_respects_remaining_token_budget(wf, settings, tmp_path, monkeypatch):
    import strands

    original_agent = strands.Agent
    observed = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            self.agent = original_agent(**kwargs)

        def __call__(self, prompt, **kwargs):
            observed.update(kwargs)
            return self.agent(prompt, **kwargs)

    monkeypatch.setattr(strands, "Agent", CapturingAgent)
    inv = StrandsInvoker(settings)
    inv._models["teddy"] = ScriptedModel([("text", "done")])
    ctx = ctx_for("teddy", wf, settings, tmp_path, token_budget=37)
    inv.invoke("teddy", "go", ctx)
    assert observed["limits"]["total_tokens"] == 37


def test_cancel_signal_stops_the_agent_and_runner_treats_it_as_cancelled(wf, settings, tmp_path):
    from legion.runner import Runner
    from legion.tools import Cancelled
    inv = StrandsInvoker(settings)
    inv._models["teddy"] = ScriptedModel([("tool", "list_files", {})])   # a model that would loop forever
    ctx = ctx_for("teddy", wf, settings, tmp_path)
    ctx.cancel.set()
    res = inv.invoke("teddy", "go", ctx)
    assert "cancel" in res.stop_reason.lower()
    with pytest.raises(Cancelled):                                       # the runner's guard after every call
        Runner(wf, settings, inv)._invoke("teddy", "go", ctx)
