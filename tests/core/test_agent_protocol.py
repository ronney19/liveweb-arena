"""Tests for FunctionCallingProtocol — parse_response and serialize_step."""

import json
from dataclasses import dataclass

import pytest

from liveweb_arena.core.agent_protocol import FunctionCallingProtocol, BROWSER_ACTIONS
from liveweb_arena.core.models import BrowserAction, BrowserObservation, TrajectoryStep
from liveweb_arena.utils.llm_client import ToolCall


@pytest.fixture
def protocol():
    return FunctionCallingProtocol()


# ── get_tools ──────────────────────────────────────────────────────

def test_get_tools_returns_all_actions(protocol):
    tools = protocol.get_tools()
    tool_names = {t["function"]["name"] for t in tools}
    assert tool_names == set(BROWSER_ACTIONS.keys())


def test_tools_have_valid_openai_format(protocol):
    for tool in protocol.get_tools():
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]


# ── parse_response: ToolCall dataclass (from chat_with_tools) ─────

def test_parse_toolcall_dataclass(protocol):
    tc = ToolCall(id="call_1", function={"name": "goto", "arguments": '{"url": "https://example.com"}'})
    action = protocol.parse_response("", [tc])
    assert action.action_type == "goto"
    assert action.params["url"] == "https://example.com"


def test_parse_toolcall_dataclass_click(protocol):
    tc = ToolCall(id="call_2", function={"name": "click", "arguments": '{"selector": "#btn"}'})
    action = protocol.parse_response("", [tc])
    assert action.action_type == "click"
    assert action.params["selector"] == "#btn"


# ── parse_response: plain dict ────────────────────────────────────

def test_parse_plain_dict(protocol):
    call = {"function": {"name": "type", "arguments": '{"selector": "#q", "text": "hello"}'}}
    action = protocol.parse_response("", [call])
    assert action.action_type == "type"
    assert action.params["text"] == "hello"


# ── parse_response: OpenAI SDK object ─────────────────────────────

def test_parse_openai_sdk_object(protocol):
    @dataclass
    class _Function:
        name: str
        arguments: str

    @dataclass
    class _ToolCall:
        function: _Function

    call = _ToolCall(function=_Function(name="scroll", arguments='{"direction": "down"}'))
    action = protocol.parse_response("", [call])
    assert action.action_type == "scroll"
    assert action.params["direction"] == "down"


# ── parse_response: stop action normalization ─────────────────────

def test_parse_stop_normalizes_params(protocol):
    tc = ToolCall(id="call_1", function={"name": "stop", "arguments": '{"answers": {"a1": "42"}}'})
    action = protocol.parse_response("", [tc])
    assert action.action_type == "stop"
    assert action.params == {"final": {"answers": {"a1": "42"}}}


# ── parse_response: edge cases ────────────────────────────────────

def test_parse_no_tool_calls_returns_none(protocol):
    assert protocol.parse_response("some text", None) is None
    assert protocol.parse_response("some text", []) is None


def test_parse_invalid_action_name_returns_none(protocol):
    tc = ToolCall(id="call_1", function={"name": "fly_to_moon", "arguments": "{}"})
    assert protocol.parse_response("", [tc]) is None


def test_parse_malformed_json_returns_none(protocol):
    tc = ToolCall(id="call_1", function={"name": "goto", "arguments": "{bad json"})
    assert protocol.parse_response("", [tc]) is None


def test_parse_uses_first_tool_call_only(protocol):
    tc1 = ToolCall(id="c1", function={"name": "goto", "arguments": '{"url": "a"}'})
    tc2 = ToolCall(id="c2", function={"name": "click", "arguments": '{"selector": "b"}'})
    action = protocol.parse_response("", [tc1, tc2])
    assert action.action_type == "goto"


# ── serialize_step ─────────────────────────────────────────────────

def _make_step(step_num, action_type, params, action_result="Success", prompt="obs"):
    obs = BrowserObservation(url="https://x.com", title="X", accessibility_tree="tree")
    action = BrowserAction(action_type=action_type, params=params)
    return TrajectoryStep(
        step_num=step_num, observation=obs, action=action,
        action_result=action_result, prompt=prompt, raw_response="",
    )


def test_serialize_goto_step(protocol):
    step = _make_step(0, "goto", {"url": "https://example.com"})
    msgs = protocol.serialize_step(step)
    assert len(msgs) == 3  # user, assistant+tool_call, tool
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["tool_calls"][0]["function"]["name"] == "goto"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["content"] == "Success"


def test_serialize_stop_denormalizes(protocol):
    step = _make_step(1, "stop", {"final": {"answers": {"a": "1"}}}, action_result="Task completed")
    msgs = protocol.serialize_step(step)
    tc = msgs[1]["tool_calls"][0]
    args = json.loads(tc["function"]["arguments"])
    assert args == {"answers": {"a": "1"}}
    assert tc["function"]["name"] == "stop"


def test_serialize_parse_failed_step(protocol):
    obs = BrowserObservation(url="https://x.com", title="X", accessibility_tree="tree")
    step = TrajectoryStep(
        step_num=0, observation=obs, action=None,
        action_result="Parse failed", prompt="obs", raw_response="garbled output",
    )
    msgs = protocol.serialize_step(step)
    assert len(msgs) == 2  # user, assistant (no tool_call)
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "garbled output"


# ── roundtrip: parse → serialize → parse ───────────────────────────

def test_roundtrip_goto(protocol):
    """解析 → 序列化 → 再解析，应该得到相同的 action。"""
    tc = ToolCall(id="call_0", function={"name": "goto", "arguments": '{"url": "https://a.com"}'})
    action = protocol.parse_response("", [tc])

    step = _make_step(0, action.action_type, action.params)
    msgs = protocol.serialize_step(step)

    # 从序列化结果重新解析
    serialized_tc = msgs[1]["tool_calls"][0]
    tc2 = ToolCall(id=serialized_tc["id"], function=serialized_tc["function"])
    action2 = protocol.parse_response("", [tc2])

    assert action2.action_type == action.action_type
    assert action2.params == action.params


def test_roundtrip_stop(protocol):
    """stop action 经过 normalize → denormalize → normalize 应一致。"""
    tc = ToolCall(id="call_0", function={"name": "stop", "arguments": '{"answers": {"q1": "yes"}}'})
    action = protocol.parse_response("", [tc])

    step = _make_step(0, action.action_type, action.params, action_result="Task completed")
    msgs = protocol.serialize_step(step)

    serialized_tc = msgs[1]["tool_calls"][0]
    tc2 = ToolCall(id=serialized_tc["id"], function=serialized_tc["function"])
    action2 = protocol.parse_response("", [tc2])

    assert action2.action_type == "stop"
    assert action2.params == action.params
