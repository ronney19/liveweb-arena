#!/usr/bin/env python3
"""
Generate a training dataset from a task list (task_id, seed pairs).

For each (task_id, seed), the script:
1. Resolves task_id to templates and generates the composite task
2. Fetches required page API data and computes ground truth for all subtasks
3. Emits both:
   - Per-subtask: one entry per subtask (task_id, seed, conversation for that subtask only)
   - Per-task: one entry per (task_id, seed) with a single conversation covering all subtasks

Conversation format: system prompt, user/assistant turns with <think> + JSON,
gotos to required URLs, then stop with answer(s). For taostats plugin, the first
navigation is always https://taostats.io so the agent visits the site before subnet pages.

Usage:
    python scripts/generate_training_dataset.py --task-list path/to/task_list.json --output dataset.json
    python scripts/generate_training_dataset.py --task-list task_list.json --output dataset.json --debug

Input: JSON array of {"task_id": int, "seed": int}
Output (when --output FILE is set):
  - FILE: per-subtask dataset (one row per subtask: task_id, seed, conversation, subtask_index, answer_tag)
  - FILE_per_task.json: per-task dataset (one row per task: task_id, seed, conversation, num_subtasks, answers)
Example: task_id 6803969 with 4 subtasks produces 4 per-subtask entries and 1 per-task entry.

Note: Some (task_id, seed) pairs may fail if GT collection does not cover all
required URLs. Successful examples are still written; exit code is 1 if any task failed.
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
sys.path.insert(0, _root)
sys.path.insert(0, _script_dir)  # so "from generate_gt import ..." finds scripts/generate_gt.py

from dotenv import load_dotenv
load_dotenv()

from liveweb_arena.plugins import get_all_plugins
from liveweb_arena.core.task_manager import TaskManager
from liveweb_arena.core.task_registry import parse_task_id, max_task_id
from liveweb_arena.core.gt_collector import GTCollector, set_current_gt_collector
from liveweb_arena.core.ground_truth_trigger import GroundTruthResult
from liveweb_arena.core.models import BrowserObservation, BrowserAction, TrajectoryStep, CompositeTask
from liveweb_arena.core.agent_policy import AgentPolicy

# Reuse URL resolution from generate_gt
from generate_gt import _required_urls_for_gt, _format_exception


def _get_plugins():
    return {k: v for k, v in get_all_plugins().items()}


def _gt_value_from_result(result: Any) -> Optional[str]:
    """Extract string value from get_ground_truth return (GroundTruthResult or raw)."""
    if isinstance(result, GroundTruthResult):
        return str(result.value) if result.success else None
    if result is None:
        return None
    return str(result)


async def _collect_ground_truth_for_task(
    task_manager: TaskManager,
    task: Any,
    templates: List[tuple],
) -> tuple:
    """
    For a composite task, fetch required URLs, feed GT collector, and return
    (answer_tag -> gt_value, ordered list of required URLs for conversation).
    """
    collector = GTCollector(subtasks=task.subtasks, task_manager=task_manager)
    set_current_gt_collector(collector)
    answers: Dict[str, Optional[str]] = {}
    ordered_urls: List[str] = []  # dedupe while preserving order

    try:
        # Visit all required URLs per subtask and merge into collector.
        # TaskManager cycles templates when num_subtasks > len(templates), so we do the same.
        for i, subtask in enumerate(task.subtasks):
            plugin_name = subtask.plugin_name
            template_tuple = templates[i % len(templates)]
            template_name = template_tuple[1]
            if not template_name:
                continue
            plugin = task_manager.get_plugin(plugin_name)
            urls = _required_urls_for_gt(plugin_name, template_name, subtask.validation_info)
            for url in urls:
                if url not in ordered_urls:
                    ordered_urls.append(url)
                try:
                    api_data = await plugin.fetch_api_data(url)
                    if api_data:
                        await collector.on_page_visit(url, "", api_data=api_data)
                except Exception as e:
                    set_current_gt_collector(None)
                    collector.cleanup()
                    raise RuntimeError(f"Failed to fetch {url}: {_format_exception(e)}") from e

        # Get ground truth for each subtask
        for subtask in task.subtasks:
            plugin = task_manager.get_plugin(subtask.plugin_name)
            result = await plugin.get_ground_truth(subtask.validation_info)
            value = _gt_value_from_result(result)
            if value is None:
                raise ValueError(
                    f"GT failed for {subtask.answer_tag}: {getattr(result, 'error', result)}"
                )
            answers[subtask.answer_tag] = value
        return answers, ordered_urls
    finally:
        set_current_gt_collector(None)
        collector.cleanup()


def _wrap_with_reasoning(raw_json: str, reasoning: str) -> str:
    """
    Wrap the action JSON in the standard reasoning format expected by the agent.
    env.py: action is "full LLM response including <think> tags".
    Policy parses JSON from the response, so reasoning can precede it.
    """
    if not reasoning.strip():
        return raw_json
    return f"<think>\n{reasoning.strip()}\n</think>\n\n{raw_json}"


def _build_goto_action_raw_response(url: str, reasoning: str = "") -> str:
    """Build the assistant raw_response for a goto action (optionally with <think> block)."""
    payload = {
        "action": {
            "type": "goto",
            "params": {"url": url},
        }
    }
    raw = json.dumps(payload, ensure_ascii=False)
    return _wrap_with_reasoning(raw, reasoning)


def _build_stop_action_raw_response(answers: Dict[str, str], reasoning: str = "") -> str:
    """Build the assistant raw_response for stop with answers (optionally with <think> block)."""
    payload = {
        "action": {
            "type": "stop",
            "params": {
                "format": "json",
                "final": {"answers": answers},
            },
        }
    }
    raw = json.dumps(payload, ensure_ascii=False)
    return _wrap_with_reasoning(raw, reasoning)


# Common Stooq ticker -> display name when validation_info has only symbol (no instruments)
_STOOQ_SYMBOL_NAMES: Dict[str, str] = {
    "nvda": "NVIDIA", "aapl": "Apple", "msft": "Microsoft", "ko": "Coca-Cola",
    "tsla": "Tesla", "dis": "Disney", "nke": "Nike", "jpm": "JPMorgan Chase",
    "intc": "Intel", "wmt": "Walmart", "cat": "Caterpillar", "c": "Citigroup",
    "us": "US", "dax": "DAX",
}


def _url_labels_for_subtask(
    plugin_name: str,
    validation_info: Dict[str, Any],
    required_urls: List[str],
) -> List[str]:
    """
    Return human-readable labels for each URL (same order as required_urls)
    so reasoning can say e.g. "NVIDIA" instead of just the URL or ticker.
    """
    labels: List[str] = []
    vi = validation_info
    from urllib.parse import urlparse, parse_qs

    def symbol_to_label(sym: str) -> str:
        """e.g. nvda.us -> NVIDIA (or NVDA if not in map), ^dax -> DAX"""
        s = str(sym).strip()
        if not s:
            return s
        base = s.split(".")[0].replace("^", "").lower() if "." in s else s.replace("^", "").lower()
        return _STOOQ_SYMBOL_NAMES.get(base, base.upper())

    if plugin_name == "stooq":
        if "symbol" in vi:
            labels = [symbol_to_label(vi["symbol"])]
        elif "symbols" in vi:
            labels = [symbol_to_label(s) for s in vi["symbols"]]
        elif "instruments" in vi:
            for item in vi["instruments"]:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    labels.append(str(item[1]))  # name e.g. "NVIDIA"
                else:
                    labels.append(symbol_to_label(item[0] if isinstance(item, (list, tuple)) else item))
        elif "group1_instruments" in vi or "group2_instruments" in vi:
            for key in ("group1_instruments", "group2_instruments"):
                for item in vi.get(key, []):
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        labels.append(str(item[1]))
                    else:
                        sym = item[0] if isinstance(item, (list, tuple)) else item
                        labels.append(symbol_to_label(sym))
        else:
            labels = [symbol_to_label(parse_qs(urlparse(u).query).get("s", [u])[0]) for u in required_urls]
    elif plugin_name == "coingecko":
        if "coin_id" in vi:
            labels = [vi["coin_id"].replace("-", " ").title()]
        elif "coin1_id" in vi and "coin2_id" in vi:
            labels = [
                vi["coin1_id"].replace("-", " ").title(),
                vi["coin2_id"].replace("-", " ").title(),
            ]
        elif "coin_ids" in vi:
            labels = [c.replace("-", " ").title() for c in vi["coin_ids"]]
        else:
            labels = []
            for u in required_urls:
                path = urlparse(u).path.rstrip("/")
                name = path.split("/")[-1].replace("-", " ").title() if path else u
                labels.append(name)
    elif plugin_name == "taostats":
        if "subnet_id" in vi:
            labels = [f"Subnet {vi['subnet_id']}"]
        elif "subnet_ids" in vi:
            labels = [f"Subnet {sid}" for sid in vi["subnet_ids"]]
        elif "netuids" in vi:
            labels = [f"Subnet {nid}" for nid in vi["netuids"]]
        else:
            labels = ["Taostats subnets"]
    elif plugin_name == "weather":
        if "location" in vi:
            labels = [vi["location"]]
        elif "city1_query" in vi and "city2_query" in vi:
            labels = [vi["city1_query"], vi["city2_query"]]
        else:
            labels = ["weather" for _ in required_urls]
    elif plugin_name == "hackernews":
        labels = ["Hacker News"]
    elif plugin_name == "openlibrary":
        if "search_query" in vi:
            labels = ["Open Library search"]
        elif "subject" in vi:
            labels = [f"Open Library ({vi['subject'].replace('_', ' ')})"]
        else:
            labels = ["Open Library"]
    else:
        labels = []
        for u in required_urls:
            path = urlparse(u).path.rstrip("/")
            name = path.split("/")[-1].replace("-", " ").title() if path else u
            labels.append(name or "page")

    # Pad or trim to match required_urls length
    while len(labels) < len(required_urls):
        idx = len(labels)
        u = required_urls[idx]
        if "stooq.com" in u:
            s = parse_qs(urlparse(u).query).get("s", [""])[0]
            labels.append(symbol_to_label(s))
        else:
            path = urlparse(u).path.rstrip("/")
            labels.append(path.split("/")[-1].replace("-", " ").title() if path else "page")
    return labels[: len(required_urls)]


def _ensure_taostats_base_url(
    plugin_name: str,
    required_urls: List[str],
    url_labels: List[str],
) -> tuple:
    """
    For taostats plugin, prepend https://taostats.io so the conversation
    navigates to the site first, then to subnet pages. Returns (urls, labels).
    """
    if plugin_name != "taostats" or not required_urls:
        return required_urls, url_labels
    first = required_urls[0].rstrip("/")
    if first == "https://taostats.io" or first == "https://www.taostats.io":
        return required_urls, url_labels
    return (
        ["https://taostats.io"] + list(required_urls),
        ["Taostats"] + list(url_labels),
    )


def _data_source_phrase(task: Any, first_url: str = "") -> str:
    """Short phrase for reasoning: which site we use (from plugin hint or URL)."""
    if task.plugin_hints:
        # e.g. "Use coingecko.com, www.coingecko.com to find information."
        hint = next(iter(task.plugin_hints.values()), "")
        for domain in ("coingecko.com", "stooq.com", "taostats.io", "wttr.in", "news.ycombinator.com", "openlibrary.org"):
            if domain in hint.lower():
                return domain
    if first_url:
        from urllib.parse import urlparse
        netloc = urlparse(first_url).netloc or first_url
        return netloc.replace("www.", "", 1) if netloc else "the appropriate website"
    return "the appropriate website"


def _minimal_obs_for_url(url: str) -> BrowserObservation:
    """Minimal observation for a page (used so step prompts are well-formed)."""
    return BrowserObservation(
        url=url,
        title="",
        accessibility_tree=f'document\n  web area "{url}"',
    )


def _single_subtask_combined_intent(intent: str) -> str:
    """Build combined_intent for a single subtask (always answer1)."""
    return """## Tasks to Complete

1. """ + intent + """
   Answer tag: answer1

## Output Requirements

When you have completed all tasks, use the "stop" action with your answers in this JSON format:

```json
{"answers": {"answer1": "..."}}
```

Each answer should be a concise, direct response to the corresponding task.
"""


def _make_single_subtask_task(subtask: Any, plugin: Any, seed: int) -> CompositeTask:
    """Build a CompositeTask with one subtask for per-subtask conversation (answer tag normalized to answer1)."""
    plugin_hints = {subtask.plugin_name: plugin.usage_hint} if hasattr(plugin, "usage_hint") else {}
    return CompositeTask(
        subtasks=[subtask],
        combined_intent=_single_subtask_combined_intent(subtask.intent),
        plugin_hints=plugin_hints,
        seed=seed,
    )


def _build_conversation(
    task: Any,
    answers: Dict[str, str],
    required_urls: List[str],
    url_labels: Optional[List[str]] = None,
) -> List[dict]:
    """
    Build conversation in the same format as env.Actor._build_conversation:
    system, then alternating user (environment) and assistant (agent) turns.
    Assistant messages use the reasoning format expected by the agent (<think> block
    then JSON); policy extracts the JSON from the full response.
    Includes goto actions to all required_urls; final turn is stop with answers.
    """
    policy = AgentPolicy()
    system_content = policy.build_system_prompt(task)
    conversation = []

    conversation.append({
        "role": "system",
        "content": system_content,
        "metadata": {
            "type": "instructions",
            "plugins": list(task.plugin_hints.keys()) if task.plugin_hints else [],
            "num_subtasks": len(task.subtasks),
        },
    })

    # Task-aware reasoning: reference style from eval conversations (plan, data source, entity names)
    intent = task.subtasks[0].intent if task.subtasks else ""
    answer_value = answers.get("answer1", "")
    data_source = _data_source_phrase(task, required_urls[0] if required_urls else "")
    labels = url_labels or []

    # max_steps = number of goto steps + 1 (stop)
    num_goto_steps = len(required_urls)
    max_steps = num_goto_steps + 1
    trajectory: List[TrajectoryStep] = []

    for step_index in range(max_steps):
        current_step = step_index + 1  # 1-based
        if step_index < num_goto_steps:
            url = required_urls[step_index]
            label = labels[step_index] if step_index < len(labels) else None
            if step_index == 0:
                obs = BrowserObservation(
                    url="about:blank",
                    title="",
                    accessibility_tree='document\n  web area "about:blank"',
                )
                if num_goto_steps > 1:
                    first_entity = f" the {label} page" if label else ""
                    goto_reasoning = (
                        f"I need to complete this task: {intent}. I'll use {data_source} for this. "
                        f"First, I'll navigate to{first_entity} to get the required information. "
                        f"I'll then visit the next required page(s) as needed. After extracting the information, I'll stop with the answer."
                    )
                else:
                    first_entity = f" the {label} page" if label else f" {url}"
                    goto_reasoning = (
                        f"I need to complete this task: {intent}. I'll use {data_source} for this. "
                        f"I'll navigate to{first_entity} to get the required information. After extracting the information, I'll stop with the answer."
                    )
            else:
                obs = _minimal_obs_for_url(required_urls[step_index - 1])
                if label:
                    goto_reasoning = (
                        f"I have the data I need from the current page. I've noted the information. "
                        f"Now I need to get {label}'s data for this task. I'll navigate to the {label} page on {data_source} to get that information."
                    )
                else:
                    goto_reasoning = (
                        f"I have the data I need from the current page. I've noted the information. "
                        f"Now I need to visit the next required page for this task. I'll navigate to {url} to get the remaining information."
                    )
            user_prompt = policy.build_step_prompt(
                obs, trajectory, current_step, max_steps, include_raw_responses=False
            )
            conversation.append({
                "role": "user",
                "content": user_prompt,
                "metadata": {"type": "environment", "step": step_index, "url": obs.url},
            })
            raw_response = _build_goto_action_raw_response(url, goto_reasoning)
            trajectory.append(TrajectoryStep(
                step_num=step_index,
                observation=obs,
                action=BrowserAction("goto", {"url": url}),
                action_result="Success",
                prompt=user_prompt,
                raw_response=raw_response,
            ))
            conversation.append({
                "role": "assistant",
                "content": raw_response,
                "metadata": {
                    "type": "agent_action",
                    "step": step_index,
                    "action_type": "goto",
                    "action_result": "Success",
                },
            })
        else:
            # Final step: prompt for stop, assistant responds with stop + answers
            obs = _minimal_obs_for_url(required_urls[-1]) if required_urls else BrowserObservation(
                url="about:blank", title="", accessibility_tree='document\n  web area "about:blank"',
            )
            user_prompt = policy.build_step_prompt(
                obs, trajectory, current_step, max_steps, include_raw_responses=False
            )
            conversation.append({
                "role": "user",
                "content": user_prompt,
                "metadata": {"type": "environment", "step": step_index, "url": obs.url},
            })
            stop_reasoning = (
                f"I have gathered the required data from the pages. "
                f"For this task, the answer is {answer_value}. Submitting the correct answer."
            )
            raw_response = _build_stop_action_raw_response(answers, stop_reasoning)
            conversation.append({
                "role": "assistant",
                "content": raw_response,
                "metadata": {
                    "type": "agent_action",
                    "step": step_index,
                    "action_type": "stop",
                    "action_result": "Task completed",
                },
            })

    return conversation


def _url_labels_for_ordered_urls(ordered_urls: List[str]) -> List[str]:
    """Build human-readable labels for a full task's ordered URL list (e.g. Taostats, Subnet 24)."""
    from urllib.parse import urlparse
    labels: List[str] = []
    for u in ordered_urls:
        parsed = urlparse(u)
        path = (parsed.path or "").strip("/")
        if "taostats.io" in (parsed.netloc or ""):
            if not path or path == "subnets":
                labels.append("Taostats")
            elif path.startswith("subnets/"):
                part = path.split("/")[-1]
                labels.append(f"Subnet {part}" if part.isdigit() else part)
            else:
                labels.append(path or "Taostats")
        elif "openlibrary.org" in (parsed.netloc or ""):
            labels.append("Open Library")
        else:
            labels.append(path.split("/")[-1].replace("-", " ").title() if path else "page")
    return labels


def _build_full_task_conversation(
    task: Any,
    answers: Dict[str, str],
    required_urls: List[str],
    url_labels: Optional[List[str]] = None,
) -> List[dict]:
    """
    Build one conversation for the full composite task: combined intent, gotos to all
    required_urls, then stop with all answers (answer1, answer2, ...).
    """
    policy = AgentPolicy()
    system_content = policy.build_system_prompt(task)
    conversation = []
    conversation.append({
        "role": "system",
        "content": system_content,
        "metadata": {
            "type": "instructions",
            "plugins": list(task.plugin_hints.keys()) if task.plugin_hints else [],
            "num_subtasks": len(task.subtasks),
        },
    })
    intent_summary = "complete the listed tasks"
    data_source = _data_source_phrase(task, required_urls[0] if required_urls else "")
    labels = url_labels or _url_labels_for_ordered_urls(required_urls)
    num_goto_steps = len(required_urls)
    max_steps = num_goto_steps + 1
    trajectory: List[TrajectoryStep] = []

    for step_index in range(max_steps):
        current_step = step_index + 1
        if step_index < num_goto_steps:
            url = required_urls[step_index]
            label = labels[step_index] if step_index < len(labels) else None
            if step_index == 0:
                obs = BrowserObservation(
                    url="about:blank",
                    title="",
                    accessibility_tree='document\n  web area "about:blank"',
                )
                if num_goto_steps > 1:
                    first_entity = f" the {label} page" if label else ""
                    goto_reasoning = (
                        f"I need to {intent_summary}. I'll use {data_source} for this. "
                        f"First, I'll navigate to{first_entity} to get the required information. "
                        f"I'll then visit the next required page(s) as needed. After extracting the information, I'll stop with the answers."
                    )
                else:
                    first_entity = f" the {label} page" if label else f" {url}"
                    goto_reasoning = (
                        f"I need to {intent_summary}. I'll use {data_source} for this. "
                        f"I'll navigate to{first_entity} to get the required information. After extracting the information, I'll stop with the answers."
                    )
            else:
                obs = _minimal_obs_for_url(required_urls[step_index - 1])
                if label:
                    goto_reasoning = (
                        f"I have the data I need from the current page. I've noted the information. "
                        f"Now I need to get {label}'s data. I'll navigate to the {label} page on {data_source} to get that information."
                    )
                else:
                    goto_reasoning = (
                        f"I have the data I need from the current page. I've noted the information. "
                        f"Now I need to visit the next required page. I'll navigate to {url} to get the remaining information."
                    )
            user_prompt = policy.build_step_prompt(
                obs, trajectory, current_step, max_steps, include_raw_responses=False
            )
            conversation.append({
                "role": "user",
                "content": user_prompt,
                "metadata": {"type": "environment", "step": step_index, "url": obs.url},
            })
            raw_response = _build_goto_action_raw_response(url, goto_reasoning)
            trajectory.append(TrajectoryStep(
                step_num=step_index,
                observation=obs,
                action=BrowserAction("goto", {"url": url}),
                action_result="Success",
                prompt=user_prompt,
                raw_response=raw_response,
            ))
            conversation.append({
                "role": "assistant",
                "content": raw_response,
                "metadata": {
                    "type": "agent_action",
                    "step": step_index,
                    "action_type": "goto",
                    "action_result": "Success",
                },
            })
        else:
            obs = _minimal_obs_for_url(required_urls[-1]) if required_urls else BrowserObservation(
                url="about:blank", title="", accessibility_tree='document\n  web area "about:blank"',
            )
            user_prompt = policy.build_step_prompt(
                obs, trajectory, current_step, max_steps, include_raw_responses=False
            )
            conversation.append({
                "role": "user",
                "content": user_prompt,
                "metadata": {"type": "environment", "step": step_index, "url": obs.url},
            })
            stop_reasoning = (
                "I have gathered the required data from all pages. Submitting the answers."
            )
            raw_response = _build_stop_action_raw_response(answers, stop_reasoning)
            conversation.append({
                "role": "assistant",
                "content": raw_response,
                "metadata": {
                    "type": "agent_action",
                    "step": step_index,
                    "action_type": "stop",
                    "action_result": "Task completed",
                },
            })

    return conversation


def _ensure_taostats_base_url_for_full_task(
    task: Any,
    ordered_urls: List[str],
) -> List[str]:
    """Prepend https://taostats.io when any subtask is taostats and first URL is not the homepage."""
    if not ordered_urls:
        return ordered_urls
    has_taostats = any(st.plugin_name == "taostats" for st in task.subtasks)
    if not has_taostats:
        return ordered_urls
    first = ordered_urls[0].rstrip("/")
    if first == "https://taostats.io" or first == "https://www.taostats.io":
        return ordered_urls
    return ["https://taostats.io"] + list(ordered_urls)


async def generate_one(
    task_id: int,
    seed: int,
    task_manager: TaskManager,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Generate one training example: task_id, seed, conversation (with correct answers).
    """
    try:
        task_config = parse_task_id(task_id)
        templates = task_config["templates"]
        num_subtasks = task_config["num_tasks"]
    except ValueError as e:
        return {
            "task_id": task_id,
            "seed": seed,
            "entries": [],
            "error": str(e),
            "success": False,
        }

    try:
        print(f"Generating composite task for task_id={task_id} seed={seed} num_subtasks={num_subtasks} templates={templates}", file=sys.stderr)
        task = await task_manager.generate_composite_task(
            seed=seed,
            num_subtasks=num_subtasks,
            templates=templates,
        )
    except Exception as e:
        if debug:
            traceback.print_exc(file=sys.stderr)
        return {
            "task_id": task_id,
            "seed": seed,
            "entries": [],
            "error": f"Task generation: {e}",
            "success": False,
        }

    try:
        answers, ordered_urls_full = await _collect_ground_truth_for_task(task_manager, task, templates)
    except Exception as e:
        if debug:
            traceback.print_exc(file=sys.stderr)
        return {
            "task_id": task_id,
            "seed": seed,
            "entries": [],
            "error": f"GT collection: {e}",
            "success": False,
        }

    # One dataset entry per subtask: same task_id and seed, conversation for that subtask only
    entries = []
    for i, subtask in enumerate(task.subtasks):
        plugin = task_manager.get_plugin(subtask.plugin_name)
        template_tuple = templates[i % len(templates)]
        template_name = template_tuple[1]
        if not template_name:
            continue
        required_urls_i = _required_urls_for_gt(
            subtask.plugin_name, template_name, subtask.validation_info
        )
        url_labels_i = _url_labels_for_subtask(
            subtask.plugin_name, subtask.validation_info, required_urls_i
        )
        required_urls_i, url_labels_i = _ensure_taostats_base_url(
            subtask.plugin_name, required_urls_i, url_labels_i
        )
        single_task = _make_single_subtask_task(subtask, plugin, seed)
        answer_value = answers.get(subtask.answer_tag) or ""
        conversation = _build_conversation(
            single_task,
            {"answer1": str(answer_value)},
            required_urls_i,
            url_labels=url_labels_i,
        )
        entries.append({
            "task_id": task_id,
            "seed": seed,
            "conversation": conversation,
            "subtask_index": i,
            "answer_tag": subtask.answer_tag,
        })

    # Per-task entry: one conversation for the full composite task (all subtasks)
    ordered_urls_full = _ensure_taostats_base_url_for_full_task(task, ordered_urls_full)
    full_labels = _url_labels_for_ordered_urls(ordered_urls_full)
    full_conversation = _build_full_task_conversation(
        task, answers, ordered_urls_full, url_labels=full_labels
    )
    per_task_entry = {
        "task_id": task_id,
        "seed": seed,
        "conversation": full_conversation,
        "num_subtasks": len(task.subtasks),
        "answers": dict(answers),
    }

    return {
        "task_id": task_id,
        "seed": seed,
        "entries": entries,
        "per_task_entry": per_task_entry,
        "error": None,
        "success": True,
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Generate training dataset from task_id/seed list for LiveWeb Arena"
    )
    parser.add_argument(
        "--task-list",
        "-i",
        type=str,
        required=True,
        metavar="FILE",
        help="JSON file: array of {task_id, seed} objects",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        metavar="FILE",
        help="Output JSON file (default: stdout)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print tracebacks on errors",
    )
    args = parser.parse_args()

    with open(args.task_list, "r", encoding="utf-8") as f:
        task_list = json.load(f)
    if not isinstance(task_list, list):
        print("Error: task list must be a JSON array", file=sys.stderr)
        sys.exit(1)
    for i, entry in enumerate(task_list):
        if not isinstance(entry, dict) or "task_id" not in entry or "seed" not in entry:
            print(f"Error: entry {i} must have 'task_id' and 'seed'", file=sys.stderr)
            sys.exit(1)
    max_tid = max_task_id()
    for i, entry in enumerate(task_list):
        tid = entry["task_id"]
        if tid < 1 or tid > max_tid:
            print(f"Error: entry {i} task_id {tid} out of range (1 to {max_tid})", file=sys.stderr)
            sys.exit(1)

    get_all_plugins()
    plugins = _get_plugins()
    task_manager = TaskManager(plugins)

    results = []
    for idx, entry in enumerate(task_list):
        task_id = entry["task_id"]
        seed = entry["seed"]
        print(f"[{idx + 1}/{len(task_list)}] task_id={task_id} seed={seed} ...", file=sys.stderr)
        one = await generate_one(task_id, seed, task_manager, debug=args.debug)
        results.append(one)
        if one.get("success"):
            n = len(one.get("entries", []))
            print(f"  ok ({n} subtask(s))", file=sys.stderr)
        else:
            print(f"  failed: {one.get('error', '')}", file=sys.stderr)

    # Per-subtask dataset: one record per subtask
    dataset = []
    dataset_per_task = []
    for r in results:
        if r.get("success") and r.get("entries"):
            for e in r["entries"]:
                dataset.append({
                    "task_id": e["task_id"],
                    "seed": e["seed"],
                    "conversation": e["conversation"],
                    "subtask_index": e["subtask_index"],
                    "answer_tag": e["answer_tag"],
                })
        if r.get("success") and r.get("per_task_entry"):
            dataset_per_task.append(r["per_task_entry"])

    out_json = json.dumps(dataset, indent=2, ensure_ascii=False, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_json)
        print(f"Wrote {len(dataset)} per-subtask examples to {args.output}", file=sys.stderr)
        # Per-task dataset: same base path with _per_task suffix
        out_base = os.path.splitext(args.output)[0] or args.output
        out_per_task = out_base + "_per_task.json"
        per_task_json = json.dumps(dataset_per_task, indent=2, ensure_ascii=False, default=str)
        with open(out_per_task, "w", encoding="utf-8") as f:
            f.write(per_task_json)
        print(f"Wrote {len(dataset_per_task)} per-task examples to {out_per_task}", file=sys.stderr)
    else:
        print(out_json)
    failed = sum(1 for r in results if not r.get("success"))
    if failed:
        print(f"Failed: {failed}/{len(results)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
