"""
Pluggable agent protocol layer.

Defines how the LLM communicates actions and how the system parses them.
Two implementations:
- LegacyTextProtocol: Original JSON-in-text format (backward compatible)
- FunctionCallingProtocol: Standard OpenAI tool_calls format (deployable)

The protocol controls:
1. What tools/actions are available to the LLM
2. How the LLM request is constructed (messages vs messages+tools)
3. How the response is parsed into BrowserAction
4. How the conversation is serialized for training data export
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from .models import BrowserAction, BrowserObservation, CompositeTask, TrajectoryStep


# Shared action definitions — single source of truth for both protocols
BROWSER_ACTIONS = {
    "goto": {
        "description": "Navigate to a URL",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    "click": {
        "description": "Click an element by CSS selector",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the element to click"},
            },
            "required": ["selector"],
        },
    },
    "type": {
        "description": "Type text into an input field",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input field"},
                "text": {"type": "string", "description": "Text to type"},
                "press_enter": {"type": "boolean", "description": "Press Enter after typing", "default": False},
            },
            "required": ["selector", "text"],
        },
    },
    "press": {
        "description": "Press a keyboard key",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to press (e.g., Enter, Escape, Tab)"},
            },
            "required": ["key"],
        },
    },
    "scroll": {
        "description": "Scroll the page",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction"},
                "amount": {"type": "integer", "description": "Pixels to scroll", "default": 300},
            },
            "required": ["direction"],
        },
    },
    "view_more": {
        "description": "View more truncated content (use when page content is cut off)",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Direction to view more"},
            },
            "required": ["direction"],
        },
    },
    "wait": {
        "description": "Wait for a duration",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "Seconds to wait", "default": 2},
            },
        },
    },
    "click_role": {
        "description": "Click an element by accessibility role and name (more stable than CSS selectors)",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Accessibility role (e.g., button, link, tab)"},
                "name": {"type": "string", "description": "Accessible name of the element"},
                "exact": {"type": "boolean", "description": "Require exact name match", "default": False},
            },
            "required": ["role", "name"],
        },
    },
    "type_role": {
        "description": "Type into an element by accessibility role and name",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Accessibility role (e.g., textbox, searchbox)"},
                "name": {"type": "string", "description": "Accessible name of the element"},
                "text": {"type": "string", "description": "Text to type"},
                "press_enter": {"type": "boolean", "description": "Press Enter after typing", "default": False},
            },
            "required": ["role", "text"],
        },
    },
    "stop": {
        "description": "Complete the task and submit final answers",
        "parameters": {
            "type": "object",
            "properties": {
                "answers": {
                    "type": "object",
                    "description": "Final answers as key-value pairs (e.g., {\"answer1\": \"value1\"})",
                },
            },
            "required": ["answers"],
        },
    },
}

VALID_ACTION_TYPES = set(BROWSER_ACTIONS.keys())


class AgentProtocol(ABC):
    """
    Abstract protocol for agent-environment interaction.

    Controls how actions are encoded in LLM requests and decoded from responses.
    """

    @abstractmethod
    def build_system_prompt(self, task: CompositeTask) -> str:
        """Build the system-level instructions for the LLM."""

    @abstractmethod
    def build_step_prompt(
        self,
        obs: BrowserObservation,
        trajectory: List[TrajectoryStep],
        current_step: int,
        max_steps: int,
    ) -> str:
        """Build the per-step user message with current observation."""

    @abstractmethod
    def get_tools(self) -> Optional[List[dict]]:
        """Return OpenAI-format tool definitions, or None for text-only protocols."""

    @abstractmethod
    def parse_response(self, raw: str, tool_calls: Optional[List[Any]] = None) -> Optional[BrowserAction]:
        """Parse LLM response (text and/or tool_calls) into a BrowserAction."""

    @abstractmethod
    def serialize_step(self, step: TrajectoryStep) -> List[dict]:
        """Serialize a trajectory step as conversation messages for training export."""


# Shared step prompt (observation format is protocol-independent)
_STEP_PROMPT_TEMPLATE = """## Current Page State

URL: {url}
Title: {title}

### Accessibility Tree
```
{accessibility_tree}
```

### Recent Actions
{recent_actions}

**Step {current_step}/{max_steps}** ({remaining_steps} steps remaining){last_step_warning}
"""

_LAST_STEP_WARNING = """

**THIS IS YOUR LAST STEP!** You MUST use the "stop" action now and provide your best answers based on the information you have gathered. Do not attempt any other action."""


def _build_step_prompt_common(
    obs: BrowserObservation,
    trajectory: List[TrajectoryStep],
    current_step: int,
    max_steps: int,
    max_recent_steps: int = 5,
    format_step_fn=None,
) -> str:
    """Shared step prompt builder used by both protocols."""
    recent = trajectory[-max_recent_steps:] if trajectory else []
    if recent:
        action_lines = []
        for step in recent:
            if format_step_fn:
                action_lines.append(format_step_fn(step))
            else:
                if step.raw_response:
                    preview = step.raw_response[:500]
                    action_lines.append(f"Step {step.step_num} response: {preview}")
                action_lines.append(f"Step {step.step_num} result: {step.action_result}")
        recent_actions = "\n".join(action_lines)
    else:
        recent_actions = "(no actions yet)"

    remaining_steps = max_steps - current_step
    last_step_warning = _LAST_STEP_WARNING if remaining_steps == 0 else ""

    return _STEP_PROMPT_TEMPLATE.format(
        url=obs.url,
        title=obs.title,
        accessibility_tree=obs.accessibility_tree,
        recent_actions=recent_actions,
        current_step=current_step,
        max_steps=max_steps,
        remaining_steps=remaining_steps,
        last_step_warning=last_step_warning,
    )


class FunctionCallingProtocol(AgentProtocol):
    """
    Standard OpenAI function calling protocol.

    Actions are defined as tools. The LLM responds with tool_calls.
    Training data exports as multi-turn with tool_call/tool messages.
    Models trained on this data can deploy with any tool-calling framework.
    """

    def __init__(self, max_recent_steps: int = 5):
        self._max_recent_steps = max_recent_steps
        self._tools = self._build_tools()

    def _build_tools(self) -> List[dict]:
        """Build OpenAI-format tool definitions from BROWSER_ACTIONS."""
        tools = []
        for name, spec in BROWSER_ACTIONS.items():
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            })
        return tools

    def build_system_prompt(self, task: CompositeTask) -> str:
        hints = ""
        if task.plugin_hints:
            hints = "## Available Information Sources\n\n"
            for _, usage_hint in task.plugin_hints.items():
                hints += usage_hint + "\n\n"

        return (
            "You are a web automation agent that interacts with real websites to complete tasks.\n\n"
            "You have access to a browser and can navigate to any website to gather information.\n"
            "Use the provided tools to interact with the browser.\n\n"
            f"{hints}"
            f"{task.combined_intent}\n\n"
            "## Tips\n\n"
            "- First analyze the task and decide which website to visit\n"
            "- Use the goto tool to navigate to the appropriate URL\n"
            "- Homepage/list data may be inaccurate. Always visit detail pages for precise values\n"
            "- When done, use the stop tool with your answers\n"
        )

    def build_step_prompt(
        self,
        obs: BrowserObservation,
        trajectory: List[TrajectoryStep],
        current_step: int = 1,
        max_steps: int = 30,
    ) -> str:
        def format_step(step: TrajectoryStep) -> str:
            if step.action:
                return (
                    f"Step {step.step_num}: {step.action.action_type}"
                    f"({json.dumps(step.action.params, ensure_ascii=False)}) → {step.action_result}"
                )
            return f"Step {step.step_num}: (parse failed) → {step.action_result}"

        prompt = _build_step_prompt_common(
            obs, trajectory, current_step, max_steps,
            self._max_recent_steps, format_step,
        )
        return prompt + "\nWhat is your next action? Use one of the available tools."

    def get_tools(self) -> List[dict]:
        return self._tools

    def parse_response(self, raw: str, tool_calls: Optional[List[Any]] = None) -> Optional[BrowserAction]:
        """Parse tool_calls from LLM response."""
        if not tool_calls:
            return None

        # Use the first tool call — handle both OpenAI SDK objects and dicts
        call = tool_calls[0]
        if hasattr(call, 'function') and hasattr(call.function, 'name'):
            # OpenAI SDK object (from streaming)
            fn_name = call.function.name
            fn_args = call.function.arguments
        elif hasattr(call, 'function') and isinstance(call.function, dict):
            # ToolCall dataclass (from chat_with_tools)
            fn_name = call.function.get("name")
            fn_args = call.function.get("arguments", "{}")
        else:
            # Plain dict
            fn_name = call.get("function", {}).get("name")
            fn_args = call.get("function", {}).get("arguments", "{}")

        if not fn_name or fn_name not in VALID_ACTION_TYPES:
            return None

        try:
            params = json.loads(fn_args) if isinstance(fn_args, str) else fn_args
        except json.JSONDecodeError:
            return None

        # Normalize stop action format for compatibility with existing agent_loop
        if fn_name == "stop":
            answers = params.get("answers", {})
            params = {"final": {"answers": answers}}

        return BrowserAction(action_type=fn_name, params=params)

    def serialize_step(self, step: TrajectoryStep) -> List[dict]:
        """Serialize as tool_call + tool response messages (standard OpenAI format)."""
        messages = []

        # User message (observation)
        if step.prompt:
            messages.append({"role": "user", "content": step.prompt})

        # Assistant message with tool_call
        if step.action:
            # Reconstruct tool call format
            if step.action.action_type == "stop":
                # Denormalize stop params back to tool format
                final = step.action.params.get("final", {})
                args = {"answers": final.get("answers", {})}
            else:
                args = step.action.params

            tool_call_id = f"call_{step.step_num}"
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": step.action.action_type,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }],
            })

            # Tool response
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": step.action_result,
            })
        else:
            # Parse failed — assistant message with raw text
            messages.append({
                "role": "assistant",
                "content": step.raw_response or "(empty response)",
            })

        return messages
