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
    # Minimal observations only:
    python scripts/generate_training_dataset.py -i task_list.json -o dataset.json --minimal-obs

Input: JSON array of {"task_id": int, "seed": int}
Output (when --output FILE is set):
  - FILE: per-subtask dataset (one row per subtask: task_id, seed, conversation, subtask_index, answer_tag)
  - FILE_per_task.json: per-task dataset (one row per task: task_id, seed, conversation, num_subtasks, answers)
  - FILE_failed.json: only if any runs fail — array of {task_id, seed, error} (override with --failed-output)
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

# Reuse URL resolution from generate_gt (same URLs and fetch order as standalone GT).
from generate_gt import _required_urls_for_gt, _format_exception

# Match eval-style datasets: embed API-bound payload in the Accessibility Tree block (see new_dataset.json).
_MAX_ACCESSIBILITY_TREE_CHARS = 120_000


def _get_plugins():
    return {k: v for k, v in get_all_plugins().items()}


def _gt_value_from_result(result: Any) -> Optional[str]:
    """Extract string value from get_ground_truth return (GroundTruthResult or raw)."""
    if isinstance(result, GroundTruthResult):
        return str(result.value) if result.success else None
    if result is None:
        return None
    return str(result)


def _canonical_visit_url(url: str) -> str:
    """
    Normalize URLs that denote the same resource so we do not visit twice.

    Taostats homepage variants (https://taostats.io, …/, www) -> https://taostats.io
    """
    u = (url or "").strip()
    if not u:
        return u
    from urllib.parse import urlparse

    p = urlparse(u)
    netloc = (p.netloc or "").lower()
    path = (p.path or "").rstrip("/")
    if netloc in ("taostats.io", "www.taostats.io") and path == "":
        return "https://taostats.io"
    return u


def _dedupe_urls_canonical(urls: List[str]) -> List[str]:
    """Preserve order; drop URLs whose canonical form was already seen."""
    out: List[str] = []
    seen: set = set()
    for u in urls:
        c = _canonical_visit_url(u)
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _dedupe_urls_and_labels(
    urls: List[str],
    labels: Optional[List[str]] = None,
) -> tuple:
    """Canonical dedupe; keep the first label for each canonical URL."""
    labs = list(labels or [])
    out_u: List[str] = []
    out_l: List[str] = []
    seen: set = set()
    for i, u in enumerate(urls):
        c = _canonical_visit_url(u)
        if c in seen:
            continue
        seen.add(c)
        out_u.append(c)
        out_l.append(labs[i] if i < len(labs) else "")
    return out_u, out_l


async def _collect_ground_truth_for_task(
    task_manager: TaskManager,
    task: Any,
    templates: List[tuple],
) -> tuple:
    """
    For a composite task, fetch required URLs, feed GT collector, and return
    (answer_tag -> gt_value, ordered list of required URLs, url -> last API payload).

    Same fetch order and URLs as generate_gt.py / _required_urls_for_gt.
    """
    collector = GTCollector(subtasks=task.subtasks, task_manager=task_manager)
    set_current_gt_collector(collector)
    answers: Dict[str, Optional[str]] = {}
    ordered_urls: List[str] = []  # dedupe while preserving order
    url_to_api: Dict[str, Any] = {}
    seen_canonical: set = set()

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
                c = _canonical_visit_url(url)
                if c in seen_canonical:
                    continue
                seen_canonical.add(c)
                ordered_urls.append(c)
                try:
                    api_data = await plugin.fetch_api_data(c)
                    if api_data:
                        url_to_api[c] = api_data
                        await collector.on_page_visit(c, "", api_data=api_data)
                except Exception as e:
                    set_current_gt_collector(None)
                    collector.cleanup()
                    raise RuntimeError(f"Failed to fetch {url}: {_format_exception(e)}") from e

        # Get ground truth for each subtask
        for i, subtask in enumerate(task.subtasks):
            template_tuple = templates[i % len(templates)]
            template_name = template_tuple[1]
            plugin = task_manager.get_plugin(subtask.plugin_name)
            result = await plugin.get_ground_truth(subtask.validation_info)
            value = _gt_value_from_result(result)

            # Special-case: CoinGecko rank sometimes lacks market_cap_rank for a coin
            # (e.g., some assets on demo API). Instead of failing the whole task,
            # fall back to an "unknown" placeholder so dataset generation can continue.
            if (
                value is None
                and subtask.plugin_name == "coingecko"
                and template_name == "coingecko_rank"
            ):
                error_msg = getattr(result, "error", "") if hasattr(result, "error") else str(result)
                if "Missing market cap rank data" in str(error_msg):
                    value = "unknown"

            if value is None:
                raise ValueError(
                    f"GT failed for {subtask.answer_tag}: {getattr(result, 'error', result)}"
                )
            answers[subtask.answer_tag] = value
        str_answers = {k: str(v) for k, v in answers.items()}
        return str_answers, ordered_urls, url_to_api
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


def _answers_summary_for_reasoning(answers: Dict[str, str]) -> str:
    """Compact string for multi-answer stop / synthesis reasoning."""
    return "; ".join(f"{k}={v}" for k, v in sorted(answers.items()))


def _api_payload_as_accessibility_tree(api_data: Any) -> str:
    """
    Content inside the step prompt's Accessibility Tree fence (eval / new_dataset.json style).
    """
    if api_data is None:
        return '[null — no API snapshot stored for this URL]'
    try:
        s = json.dumps(api_data, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        s = json.dumps(str(api_data), ensure_ascii=False)
    if len(s) > _MAX_ACCESSIBILITY_TREE_CHARS:
        s = s[:_MAX_ACCESSIBILITY_TREE_CHARS] + "\n... (truncated)"
    return s


def _observation_for_training_url(url: str, api_data: Any, *, rich: bool) -> BrowserObservation:
    """Build observation: rich JSON tree like new_dataset.json, or minimal web-area line."""
    if url == "about:blank":
        if rich:
            tree = (
                "document\n  heading \"Start\"\n  StaticText \"No page loaded yet. "
                "Next action should navigate to the first required URL for this task.\""
            )
        else:
            tree = 'document\n  web area "about:blank"'
        return BrowserObservation(url=url, title="", accessibility_tree=tree)
    if rich:
        tree = _api_payload_as_accessibility_tree(api_data)
        return BrowserObservation(url=url, title="(loaded)", accessibility_tree=tree)
    return BrowserObservation(
        url=url,
        title="(loaded)",
        accessibility_tree=f'document\n  web area "{url}"',
    )


def _infer_plugin_name_for_url(url: str) -> Optional[str]:
    """Best-effort plugin for extra fetches (e.g. taostats.io prepended in training but not in GT list)."""
    from urllib.parse import urlparse

    host = (urlparse(url).netloc or "").lower()
    if "taostats" in host:
        return "taostats"
    if "coingecko" in host:
        return "coingecko"
    if "stooq" in host:
        return "stooq"
    if "ycombinator" in host or host.endswith("news.ycombinator.com"):
        return "hackernews"
    if "openlibrary" in host:
        return "openlibrary"
    if "wttr.in" in host:
        return "weather"
    if "open-meteo" in host:
        return "openmeteo"
    return None


async def _fill_url_snapshots(
    task_manager: TaskManager,
    urls: List[str],
    url_to_api: Dict[str, Any],
) -> None:
    """Fetch API data for any URL missing from url_to_api (mutates url_to_api)."""
    for url in urls:
        if url_to_api.get(url) is not None:
            continue
        pname = _infer_plugin_name_for_url(url)
        if not pname:
            continue
        try:
            plugin = task_manager.get_plugin(pname)
            data = await plugin.fetch_api_data(url)
            if data:
                url_to_api[url] = data
        except Exception:
            pass


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
    elif plugin_name == "openmeteo":
        if "city_name" in vi:
            labels = [vi["city_name"]]
        elif "city1_name" in vi and "city2_name" in vi:
            labels = [vi["city1_name"], vi["city2_name"]]
        else:
            labels = ["Open Meteo" for _ in required_urls]
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
        elif "open-meteo.com" in u:
            labels.append("Open Meteo")
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
    Merges https://taostats.io and https://taostats.io/ so the homepage is not visited twice.
    """
    if plugin_name != "taostats" or not required_urls:
        return required_urls, url_labels
    first_c = _canonical_visit_url(required_urls[0])
    if first_c == "https://taostats.io":
        return _dedupe_urls_and_labels(required_urls, url_labels)
    merged_u = ["https://taostats.io"] + list(required_urls)
    merged_l = ["Taostats"] + list(url_labels)
    while len(merged_l) < len(merged_u):
        merged_l.append("")
    return _dedupe_urls_and_labels(merged_u, merged_l)


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


def _truncate_reasoning_text(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 20].rstrip() + "\n... (truncated)"


def _taostats_detail_netuid_from_url(url: str) -> Optional[str]:
    """If URL is .../subnets/<n>, return netuid string; else None (listing / home)."""
    from urllib.parse import urlparse

    path = (urlparse(url).path or "").strip("/")
    segs = path.split("/")
    if len(segs) >= 2 and segs[0] == "subnets" and segs[1].isdigit():
        return segs[1]
    return None


def _taostats_focus_netuids_from_urls(urls: List[str]) -> List[str]:
    """Netuids from all /subnets/<n> URLs in this trajectory (deduped, order preserved)."""
    out: List[str] = []
    for u in urls:
        n = _taostats_detail_netuid_from_url(u)
        if not n:
            continue
        try:
            ns = str(int(n))
        except ValueError:
            ns = str(n)
        if ns not in out:
            out.append(ns)
    return out


def _normalize_api_data_for_reasoning(api_data: Any) -> Any:
    """Parse JSON strings so we never fall back to str(huge dict)."""
    if isinstance(api_data, str):
        s = api_data.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return api_data
    return api_data


def _format_api_extract_for_reasoning(
    url: str,
    api_data: Any,
    max_len: int = 420,
    focus_netuids: Optional[List[str]] = None,
) -> str:
    """
    Short natural-language summary of API payload for assistant reasoning (not full JSON).
    """
    if api_data is None:
        return ""
    api_data = _normalize_api_data_for_reasoning(api_data)
    from urllib.parse import urlparse

    host = (urlparse(url).netloc or "").lower()

    def out(x: str) -> str:
        return _truncate_reasoning_text(x, max_len)

    try:
        if isinstance(api_data, dict):
            # wttr.in / weather-style
            cc = api_data.get("current_condition")
            if isinstance(cc, list) and cc and isinstance(cc[0], dict):
                row = cc[0]
                wdesc = row.get("weatherDesc")
                if isinstance(wdesc, list) and wdesc and isinstance(wdesc[0], dict):
                    wtxt = str(wdesc[0].get("value", ""))
                else:
                    wtxt = str(wdesc or "")
                parts = [
                    f"temp_C={row.get('temp_C')}",
                    f"temp_F={row.get('temp_F')}",
                    f"humidity={row.get('humidity')}",
                    f"weather={wtxt}",
                ]
                return out("weather: " + ", ".join(p for p in parts if not p.endswith("=None") and p.split("=", 1)[-1] != ""))

            # CoinGecko coin page
            if "coingecko" in host:
                md = api_data.get("market_data") or {}
                cp = md.get("current_price") or {}
                line = []
                if isinstance(cp, dict) and cp:
                    line.append(f"current_price_usd={cp.get('usd')}")
                ch24 = md.get("price_change_percentage_24h")
                if ch24 is not None:
                    line.append(f"24h_change%={ch24}")
                rank = api_data.get("market_cap_rank")
                if rank is not None:
                    line.append(f"market_cap_rank={rank}")
                name = api_data.get("name") or api_data.get("id")
                if name:
                    line.insert(0, f"name={name}")
                if line:
                    return out("coingecko: " + ", ".join(line))

            # Taostats: detail page is a flat dict; list/home is {"subnets": {...}}.
            if "taostats" in host:
                subs = api_data.get("subnets") if isinstance(api_data.get("subnets"), dict) else None
                is_roster = isinstance(subs, dict) and subs and set(api_data.keys()) <= {"subnets"}
                # Flat subnet detail (fetch_single_subnet_data) — not the full roster object
                if not is_roster and api_data.get("netuid") is not None:
                    keys = ("netuid", "name", "price", "market_cap", "emission", "volume_24h")
                    line = [f"{k}={api_data.get(k)}" for k in keys if api_data.get(k) is not None]
                    if line:
                        return out("subnet detail: " + ", ".join(line))
                if isinstance(subs, dict) and subs:
                    want = _taostats_detail_netuid_from_url(url)
                    one: Any = None
                    picked: Optional[str] = None
                    if want is not None:
                        one = subs.get(want) or subs.get(str(want))
                        if one is None:
                            for k, v in subs.items():
                                if isinstance(v, dict) and str(v.get("netuid")) == str(want):
                                    one, picked = v, str(k)
                                    break
                        else:
                            picked = want
                        if isinstance(one, dict):
                            line = [
                                f"netuid={one.get('netuid')}",
                                f"name={one.get('name')}",
                                f"price={one.get('price')}",
                            ]
                            return out(
                                f"subnet {picked or one.get('netuid')}: "
                                + ", ".join(
                                    str(x)
                                    for x in line
                                    if x.split("=", 1)[-1] not in ("None", "")
                                )
                            )
                        return out(
                            f"subnet URL netuid={want}: no row in roster "
                            f"({len(subs)} subnets in snapshot — see tree)"
                        )
                    # Listing / home — never sample netuid 1/Apex; name task subnets from planned URLs
                    focus = list(focus_netuids or [])
                    if focus:
                        parts: List[str] = []
                        for nid in focus[:16]:
                            row = subs.get(nid) or subs.get(str(nid))
                            if isinstance(row, dict):
                                nm = row.get("name", "?")
                                parts.append(
                                    f"SN{nid} {nm}: price={row.get('price')}, tao_in={row.get('tao_in')}"
                                )
                            else:
                                parts.append(f"SN{nid}: not in loaded roster")
                        return out("task-relevant subnets: " + "; ".join(parts))
                    return out(
                        f"subnets roster: {len(subs)} subnets (listing — see tree or "
                        f"detail URLs; no arbitrary subnet sample in reasoning)"
                    )
                return out("taostats: empty or unknown snapshot shape (see accessibility tree).")

            # Stooq-style (often wrapped or flat)
            if "stooq" in host:
                if "symbol" in api_data or "close" in api_data:
                    keys = ("symbol", "name", "close", "volume", "time")
                    line = [f"{k}={api_data.get(k)}" for k in keys if api_data.get(k) is not None]
                    if line:
                        return out("quote: " + ", ".join(line))

            # Hacker News (common shapes)
            if "ycombinator" in host or "hackernews" in host:
                hits = api_data.get("hits") or api_data.get("stories")
                if isinstance(hits, list):
                    return out(f"stories count={len(hits)} (listing page)")
                return out("hn: top-level keys " + ", ".join(list(api_data.keys())[:12]))

            # Open Library search
            if "openlibrary" in host:
                docs = api_data.get("docs")
                if isinstance(docs, list) and docs:
                    d0 = docs[0] if isinstance(docs[0], dict) else {}
                    title = d0.get("title", "")
                    ed = d0.get("edition_count", "")
                    return out(f"first hit title={title!r}, edition_count={ed}")
                return out("openlibrary keys: " + ", ".join(list(api_data.keys())[:15]))

            # Open-Meteo style (often daily time series)
            if "open-meteo" in host or "daily" in api_data or "hourly" in api_data:
                daily = api_data.get("daily") or {}
                if isinstance(daily, dict):
                    tmax = daily.get("temperature_2m_max")
                    if isinstance(tmax, list) and tmax:
                        return out(f"daily temperature_2m_max (sample first days)={tmax[:5]}")
                return out("open-meteo keys: " + ", ".join(list(api_data.keys())[:12]))

        # Generic shallow digest
        if isinstance(api_data, dict):
            bits: List[str] = []
            for k, v in list(api_data.items())[:18]:
                if isinstance(v, (str, int, float, bool)) or v is None:
                    bits.append(f"{k}={v!r}")
                elif isinstance(v, list):
                    bits.append(f"{k}=[len {len(v)}]")
                elif isinstance(v, dict):
                    bits.append(f"{k}={{…{len(v)} keys}}")
            if bits:
                return out("snapshot: " + ", ".join(bits))
        if isinstance(api_data, list):
            return out(f"array length={len(api_data)}")
    except Exception:
        if "taostats" in host:
            return out("taostats: could not summarize snapshot (use accessibility tree).")
    if "taostats" in host:
        return out("taostats: use accessibility tree for this page (avoid inlining full roster).")
    return out(str(api_data)[:max_len])


def _default_answer_tags_per_url(
    required_urls: List[str],
    answers: Dict[str, str],
) -> Dict[str, List[str]]:
    """Single-subtask default: every URL contributes to the sole answer tag."""
    if len(answers) != 1:
        return {}
    tag = next(iter(answers.keys()))
    return {u: [tag] for u in required_urls}


def _tags_finalized_at_url_index(
    required_urls: List[str],
    answer_tags_per_url: Dict[str, List[str]],
) -> Dict[int, List[str]]:
    """
    For each step index, which answer tags are "settled" on that page (last required
    visit that lists the tag in answer_tags_per_url).
    """
    tag_to_last_idx: Dict[str, int] = {}
    for i, u in enumerate(required_urls):
        for tag in answer_tags_per_url.get(u, []):
            tag_to_last_idx[tag] = max(tag_to_last_idx.get(tag, -1), i)
    index_to_tags: Dict[int, List[str]] = {}
    for tag, idx in tag_to_last_idx.items():
        index_to_tags.setdefault(idx, []).append(tag)
    for idx in index_to_tags:
        index_to_tags[idx] = sorted(set(index_to_tags[idx]))
    return index_to_tags


def _build_answer_tags_per_url_full_task(task: Any, templates: List[tuple]) -> Dict[str, List[str]]:
    """Map each GT URL to answer tag(s) for full composite trajectories."""
    m: Dict[str, List[str]] = {}
    for i, st in enumerate(task.subtasks):
        template_tuple = templates[i % len(templates)]
        template_name = template_tuple[1]
        if not template_name:
            continue
        urls = _required_urls_for_gt(st.plugin_name, template_name, st.validation_info)
        labs = _url_labels_for_subtask(st.plugin_name, st.validation_info, urls)
        urls, _ = _ensure_taostats_base_url(st.plugin_name, urls, labs)
        for u in urls:
            m.setdefault(u, []).append(st.answer_tag)
    for u in m:
        m[u] = sorted(set(m[u]))
    return m


def _digest_reasoning_parts(
    url: str,
    label: Optional[str],
    snap: Any,
    answers: Dict[str, str],
    finalized_tags_here: List[str],
    *,
    page_index_1based: int,
    num_pages: int,
    trajectory_urls: Optional[List[str]] = None,
) -> tuple:
    """
    Returns (snapshot_highlights_line, digest_body) for assistant reasoning on goto/stop.
    """
    focus = _taostats_focus_netuids_from_urls(trajectory_urls or [])
    api_bits = _format_api_extract_for_reasoning(url, snap, focus_netuids=focus)
    highlights = ""
    if api_bits:
        highlights = f"Notable values in this snapshot: {api_bits}"

    digest_lines: List[str] = [
        f"{label or url}:",
    ]
    if api_bits:
        digest_lines.append(f"  • From API/tree: {api_bits}")
    gt_parts: List[str] = []
    for tag in finalized_tags_here:
        if tag in answers:
            gt_parts.append(f"{tag}={answers[tag]!r}")
    if gt_parts:
        digest_lines.append(
            "  • Extracted ground-truth answer(s) tied to this page: " + "; ".join(gt_parts)
        )
    if not api_bits and not gt_parts:
        digest_lines.append(
            "  • Parsed this page; no compact field summary available (see full tree)."
        )
    digest_body = "\n".join(digest_lines)
    return highlights, digest_body


def _build_conversation(
    task: Any,
    answers: Dict[str, str],
    required_urls: List[str],
    url_labels: Optional[List[str]] = None,
    url_to_api: Optional[Dict[str, Any]] = None,
    *,
    rich_observations: bool = True,
    planning_intent: Optional[str] = None,
    stop_answer_summary: Optional[str] = None,
    answer_tags_per_url: Optional[Dict[str, List[str]]] = None,
) -> List[dict]:
    """
    Build conversation like env.Actor: user sees Accessibility Tree + step prompt.

    Uses the same API payloads as GT collection (url_to_api) inside the tree when
    rich_observations=True (eval-style / new_dataset.json).

    One assistant turn per landed page: observation + digest + goto or stop (no wait actions).

    planning_intent / stop_answer_summary override the default single-subtask
    phrasing for full-composite-task conversations.
    """
    policy = AgentPolicy()
    system_content = policy.build_system_prompt(task)
    conversation: List[dict] = []
    url_to_api = url_to_api or {}
    labels = url_labels or []

    conversation.append({
        "role": "system",
        "content": system_content,
        "metadata": {
            "type": "instructions",
            "plugins": list(task.plugin_hints.keys()) if task.plugin_hints else [],
            "num_subtasks": len(task.subtasks),
        },
    })

    intent = planning_intent if planning_intent is not None else (
        task.subtasks[0].intent if task.subtasks else ""
    )
    answer_value = (
        stop_answer_summary
        if stop_answer_summary is not None
        else answers.get("answer1", "")
    )
    data_source = _data_source_phrase(task, required_urls[0] if required_urls else "")
    n = len(required_urls)
    utags = (
        answer_tags_per_url
        if answer_tags_per_url is not None
        else _default_answer_tags_per_url(required_urls, answers)
    )
    finalized_at = _tags_finalized_at_url_index(required_urls, utags) if utags else {}
    trajectory: List[TrajectoryStep] = []
    step_idx = 0  # TrajectoryStep.step_num

    def _append_pair(
        obs: BrowserObservation,
        raw_response: str,
        action: BrowserAction,
        action_type: str,
        env_step: int,
    ) -> None:
        nonlocal step_idx
        current_step = len(trajectory) + 1
        user_prompt = policy.build_step_prompt(
            obs, trajectory, current_step, total_user_steps, include_raw_responses=False
        )
        conversation.append({
            "role": "user",
            "content": user_prompt,
            "metadata": {"type": "environment", "step": env_step, "url": obs.url},
        })
        conversation.append({
            "role": "assistant",
            "content": raw_response,
            "metadata": {
                "type": "agent_action",
                "step": env_step,
                "action_type": action_type,
                "action_result": "Success",
            },
        })
        trajectory.append(TrajectoryStep(
            step_num=step_idx,
            observation=obs,
            action=action,
            action_result="Success",
            prompt=user_prompt,
            raw_response=raw_response,
        ))
        step_idx += 1

    if n == 0:
        total_user_steps = 1
        obs = _observation_for_training_url("about:blank", None, rich=False)
        stop_reasoning = (
            f"Planning: There is no external page to visit for this task.\n"
            f"Task: {intent}\n"
            f"Conclusion: The answer is {answer_value}."
        )
        _append_pair(
            obs,
            _build_stop_action_raw_response(answers, stop_reasoning),
            BrowserAction("stop", {"format": "json", "final": {"answers": answers}}),
            "stop",
            0,
        )
        return conversation

    total_user_steps = n + 1
    blank_tree = (
        'document\n  web area "about:blank"'
        if not rich_observations
        else (
            "document\n  heading \"Start\"\n  StaticText \"No page loaded yet. "
            "Next action should navigate to the first required URL for this task.\""
        )
    )
    obs0 = BrowserObservation(url="about:blank", title="", accessibility_tree=blank_tree)
    label0 = labels[0] if labels else None
    u0 = required_urls[0]
    if n > 1:
        plan = (
            f"Planning: I must complete: {intent}\n"
            f"Data source: {data_source}.\n"
            f"Step 1: Open the first target page"
            f"{(' (' + str(label0) + ')') if label0 else ''} at the required URL.\n"
            f"I will read the accessibility tree (API-backed snapshot), note facts, then visit remaining URLs if any."
        )
    else:
        plan = (
            f"Planning: Single-page task — {intent}\n"
            f"Data source: {data_source}.\n"
            f"I will open the required page, read the tree for ground-truth fields, then stop with the answer."
        )
    goto0 = _build_goto_action_raw_response(
        u0,
        plan + f"\n\nAction: navigate to {u0}.",
    )
    _append_pair(obs0, goto0, BrowserAction("goto", {"url": u0}), "goto", 0)

    for i in range(n):
        ui = required_urls[i]
        li = labels[i] if i < len(labels) else None
        snap = url_to_api.get(ui)
        rich_obs = _observation_for_training_url(ui, snap, rich=rich_observations)
        finalized_here = finalized_at.get(i, [])
        highlights, digest_body = _digest_reasoning_parts(
            ui,
            li,
            snap,
            answers,
            finalized_here,
            page_index_1based=i + 1,
            num_pages=n,
            trajectory_urls=required_urls,
        )
        obs_pass = (
            f"I read the Accessibility Tree (structured API snapshot) and extract task-relevant fields.\n"
            f"Next: {'navigate to the next required URL' if i < n - 1 else 'submit answers with stop'}."
        )
        if highlights:
            obs_pass += f"\n{highlights}"
        digest_reasoning = (
            f"{digest_body}\n"
            f"Remaining navigations: {max(0, n - 1 - i)} after this step."
        )
        if i < n - 1:
            nxt = required_urls[i + 1]
            ln = labels[i + 1] if i + 1 < len(labels) else None
            full_reasoning = (
                f"{obs_pass}\n\n{digest_reasoning}\n\n"
                f"Planning: Next I need data from {ln or nxt}.\n"
                f"Action: goto {nxt}."
            )
            _append_pair(
                rich_obs,
                _build_goto_action_raw_response(nxt, full_reasoning),
                BrowserAction("goto", {"url": nxt}),
                "goto",
                step_idx,
            )
        else:
            full_reasoning = (
                f"{obs_pass}\n\n{digest_reasoning}\n\n"
                f"Synthesis: Combined what I need from all visited pages for: {intent}\n"
                f"Final answer: {answer_value}.\n"
                f"Action: stop with JSON answers."
            )
            _append_pair(
                rich_obs,
                _build_stop_action_raw_response(answers, full_reasoning),
                BrowserAction("stop", {"format": "json", "final": {"answers": answers}}),
                "stop",
                step_idx,
            )

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
        elif "open-meteo.com" in (parsed.netloc or ""):
            from urllib.parse import parse_qs
            q = parse_qs(parsed.query or "")
            lat = q.get("latitude", [""])[0]
            lon = q.get("longitude", [""])[0]
            if lat and lon:
                labels.append(f"Open Meteo ({lat},{lon})")
            else:
                labels.append("Open Meteo")
        else:
            labels.append(path.split("/")[-1].replace("-", " ").title() if path else "page")
    return labels


def _build_full_task_conversation(
    task: Any,
    answers: Dict[str, str],
    required_urls: List[str],
    url_labels: Optional[List[str]] = None,
    url_to_api: Optional[Dict[str, Any]] = None,
    *,
    rich_observations: bool = True,
    answer_tags_per_url: Optional[Dict[str, List[str]]] = None,
) -> List[dict]:
    """
    One conversation for the full composite task: same trajectory style as per-subtask
    (_build_conversation), with planning/synthesis text covering all answer tags.
    """
    labels = url_labels or _url_labels_for_ordered_urls(required_urls)
    n_st = len(task.subtasks)
    preview: List[str] = []
    for st in task.subtasks[:6]:
        snippet = (st.intent or "").replace("\n", " ").strip()
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        preview.append(f"({st.answer_tag}) {snippet}")
    if n_st > 6:
        preview.append("...")
    planning = (
        f"Complete all {n_st} subtasks from the system task list in order. "
        f"Subtasks: {' | '.join(preview)}"
    )
    return _build_conversation(
        task,
        answers,
        required_urls,
        url_labels=labels,
        url_to_api=url_to_api,
        rich_observations=rich_observations,
        planning_intent=planning,
        stop_answer_summary=_answers_summary_for_reasoning(answers),
        answer_tags_per_url=answer_tags_per_url,
    )


def _ensure_taostats_base_url_for_full_task(
    task: Any,
    ordered_urls: List[str],
) -> List[str]:
    """Prepend https://taostats.io when any subtask is taostats and first URL is not the homepage."""
    if not ordered_urls:
        return ordered_urls
    has_taostats = any(st.plugin_name == "taostats" for st in task.subtasks)
    if not has_taostats:
        return _dedupe_urls_canonical(list(ordered_urls))
    first_c = _canonical_visit_url(ordered_urls[0])
    if first_c == "https://taostats.io":
        return _dedupe_urls_canonical(list(ordered_urls))
    merged = ["https://taostats.io"] + list(ordered_urls)
    return _dedupe_urls_canonical(merged)


async def generate_one(
    task_id: int,
    seed: int,
    task_manager: TaskManager,
    debug: bool = False,
    *,
    rich_observations: bool = True,
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
        answers, ordered_urls_full, url_to_api = await _collect_ground_truth_for_task(
            task_manager, task, templates
        )
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

    # Union every URL that appears in per-subtask or full-task trajectories, then
    # fill snapshots (e.g. prepended https://taostats.io) using the same fetch as GT.
    urls_union: List[str] = []

    def _add_urls(urls: List[str]) -> None:
        for u in urls:
            if u not in urls_union:
                urls_union.append(u)

    _add_urls(ordered_urls_full)
    per_subtask_pack: List[tuple] = []
    for i, subtask in enumerate(task.subtasks):
        plugin = task_manager.get_plugin(subtask.plugin_name)
        template_tuple = templates[i % len(templates)]
        template_name = template_tuple[1]
        if not template_name:
            per_subtask_pack.append((None, None))
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
        _add_urls(required_urls_i)
        per_subtask_pack.append((required_urls_i, url_labels_i))

    ordered_urls_full = _ensure_taostats_base_url_for_full_task(task, ordered_urls_full)
    _add_urls(ordered_urls_full)
    await _fill_url_snapshots(task_manager, urls_union, url_to_api)

    # One dataset entry per subtask: same task_id and seed, conversation for that subtask only
    entries = []
    for i, subtask in enumerate(task.subtasks):
        plugin = task_manager.get_plugin(subtask.plugin_name)
        template_tuple = templates[i % len(templates)]
        template_name = template_tuple[1]
        if not template_name:
            continue
        packed = per_subtask_pack[i] if i < len(per_subtask_pack) else (None, None)
        required_urls_i, url_labels_i = packed
        if required_urls_i is None:
            continue
        single_task = _make_single_subtask_task(subtask, plugin, seed)
        answer_value = answers.get(subtask.answer_tag) or ""
        conversation = _build_conversation(
            single_task,
            {"answer1": str(answer_value)},
            required_urls_i,
            url_labels=url_labels_i,
            url_to_api=url_to_api,
            rich_observations=rich_observations,
        )
        entries.append({
            "task_id": task_id,
            "seed": seed,
            "conversation": conversation,
            "subtask_index": i,
            "answer_tag": subtask.answer_tag,
        })

    # Per-task entry: one conversation for the full composite task (all subtasks)
    full_labels = _url_labels_for_ordered_urls(ordered_urls_full)
    full_answer_tags = _build_answer_tags_per_url_full_task(task, templates)
    full_conversation = _build_full_task_conversation(
        task,
        answers,
        ordered_urls_full,
        url_labels=full_labels,
        url_to_api=url_to_api,
        rich_observations=rich_observations,
        answer_tags_per_url=full_answer_tags,
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
        "--failed-output",
        type=str,
        default=None,
        metavar="FILE",
        help=(
            "JSON file listing failed {task_id, seed, error} entries. "
            "Default when using -o: <output_basename>_failed.json (only written if any failures)."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print tracebacks on errors",
    )
    parser.add_argument(
        "--minimal-obs",
        action="store_true",
        dest="minimal_obs",
        help="Use minimal web-area observations instead of API JSON in Accessibility Tree",
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
        one = await generate_one(
            task_id,
            seed,
            task_manager,
            debug=args.debug,
            rich_observations=not args.minimal_obs,
        )
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

    failed_rows = [
        {
            "task_id": r["task_id"],
            "seed": r["seed"],
        }
        for r in results
        if not r.get("success")
    ]

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

    failed_path = args.failed_output
    if failed_path is None and args.output and failed_rows:
        failed_path = (os.path.splitext(args.output)[0] or args.output) + "_failed.json"
    if failed_path:
        failed_json = json.dumps(failed_rows, indent=2, ensure_ascii=False, default=str)
        with open(failed_path, "w", encoding="utf-8") as f:
            f.write(failed_json)
        print(
            f"Wrote {len(failed_rows)} failed task(s) to {failed_path}",
            file=sys.stderr,
        )

    failed = len(failed_rows)
    if failed:
        print(f"Failed: {failed}/{len(results)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
