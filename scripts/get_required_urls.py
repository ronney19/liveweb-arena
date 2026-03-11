#!/usr/bin/env python3
"""
Get required URLs for a specific task_id and seed.

Uses the same task generation as eval.py so the URLs match what the validator
expects. Outputs one URL per line (or JSON) for use in scripts/checks.

Usage:
    python scripts/get_required_urls.py --task-id 32312297 --seed 1442009305
    python scripts/get_required_urls.py --task-id 32312297 --seed 1442009305 --json
    python scripts/get_required_urls.py --task-id 32312297  # uses variation_seed from task_id

Run from repo root; use the project venv if plugins fail to load (e.g. .venv/bin/python).
"""

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Set

# Add project root for imports
_script_dir = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(_script_dir.parent))

from liveweb_arena.plugins import get_all_plugins
from liveweb_arena.core.task_manager import TaskManager
from liveweb_arena.core.task_registry import TaskRegistry, parse_task_id


def _extract_urls_from_validation_info(plugin_name: str, validation_info: Dict[str, Any]) -> List[str]:
    """
    Build required URLs from plugin name and validation_info.
    Mirrors the URL patterns used in templates/utils when they report "Required URL".
    """
    urls: List[str] = []
    seen: Set[str] = set()

    def add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    if not validation_info:
        return urls

    info = validation_info

    if plugin_name == "taostats":
        if "subnet_id" in info:
            sid = info["subnet_id"]
            add(f"https://www.taostats.io/subnet/{sid}")
        for sid in info.get("subnet_ids", []):
            add(f"https://www.taostats.io/subnet/{sid}")
        if "subnet_ids" in info or "subnet_id" in info:
            add("https://taostats.io/")
            add("https://taostats.io/subnets")

    elif plugin_name == "coingecko":
        if "coin_id" in info:
            add(f"https://www.coingecko.com/en/coins/{info['coin_id']}")
        for a in info.get("coins", []) or []:
            cid = a.get("coin_id") or a.get("id") if isinstance(a, dict) else None
            if cid:
                add(f"https://www.coingecko.com/en/coins/{cid}")

    elif plugin_name == "stooq":
        if "symbol" in info:
            add(f"https://stooq.com/q/?s={info['symbol']}")
        for a in info.get("symbols", []) or []:
            sym = a if isinstance(a, str) else (a.get("symbol") if isinstance(a, dict) else None)
            if sym:
                add(f"https://stooq.com/q/?s={sym}")

    elif plugin_name == "weather":
        loc = info.get("location") or info.get("city1_query") or info.get("query")
        if loc:
            from urllib.parse import quote
            add(f"https://wttr.in/{quote(str(loc).replace(' ', '+'))}")
        for key in ("city1_query", "city2_query"):
            if key in info and info[key]:
                from urllib.parse import quote
                add(f"https://wttr.in/{quote(str(info[key]).replace(' ', '+'))}")

    elif plugin_name == "openlibrary":
        # book_stats: search by book title
        if "search_query" in info:
            q = str(info["search_query"]).replace(" ", "+")
            add(f"https://openlibrary.org/search?q={q}")
        # subject_multi_condition: subject search sorted by editions
        if "subject" in info:
            subj = str(info["subject"]).replace("_", "+")
            add(f"https://openlibrary.org/search?q={subj}&sort=editions")
        add("https://openlibrary.org/")

    elif plugin_name == "hybrid":
        # Crypto (CoinGecko): level1_asset, level2_asset, condition_asset only
        for key in ("level1_asset", "level2_asset", "condition_asset"):
            asset = info.get(key)
            if isinstance(asset, dict) and asset.get("asset_id"):
                add(f"https://www.coingecko.com/en/coins/{asset['asset_id']}")
        # targets: branch -> { symbol } → Stooq (stocks, not coins)
        targets = info.get("targets") or {}
        if isinstance(targets, dict):
            for branch, target in targets.items():
                if isinstance(target, dict) and target.get("symbol"):
                    add(f"https://stooq.com/q/?s={target['symbol']}")
        # positive_target / negative_target: Stooq only
        for key in ("positive_target", "negative_target"):
            target = info.get(key)
            if isinstance(target, dict) and target.get("symbol"):
                add(f"https://stooq.com/q/?s={target['symbol']}")
        # assets list: source "coingecko" -> asset_id, source "stooq" -> symbol
        for a in info.get("assets", []) or []:
            if not isinstance(a, dict):
                continue
            src = a.get("source", "")
            if src == "coingecko" and a.get("asset_id"):
                add(f"https://www.coingecko.com/en/coins/{a['asset_id']}")
            elif src == "stooq" and a.get("symbol"):
                add(f"https://stooq.com/q/?s={a['symbol']}")
        # cross_domain_calc: high_diff_asset, low_diff_asset, medium_asset (have source)
        for key in ("high_diff_asset", "low_diff_asset", "medium_asset"):
            asset = info.get(key)
            if isinstance(asset, dict):
                if asset.get("asset_id"):
                    add(f"https://www.coingecko.com/en/coins/{asset['asset_id']}")
                if asset.get("symbol"):
                    add(f"https://stooq.com/q/?s={asset['symbol']}")

    return urls


async def get_required_urls(task_id: int, seed: int) -> Dict[str, Any]:
    """
    Generate the task for (task_id, seed) and return required URLs per subtask and flattened.
    """
    config = parse_task_id(task_id)
    templates = config["templates"]
    num_tasks = config["num_tasks"]
    # Use provided seed (caller can pass config["variation_seed"] to match task_id default)
    task_manager = TaskManager(get_all_plugins())
    task = await task_manager.generate_composite_task(
        seed=seed,
        num_subtasks=num_tasks,
        templates=templates,
    )

    by_subtask: List[Dict[str, Any]] = []
    all_urls: List[str] = []

    for i, st in enumerate(task.subtasks):
        urls = _extract_urls_from_validation_info(st.plugin_name, st.validation_info)
        entry = {
            "answer_tag": st.answer_tag,
            "plugin": st.plugin_name,
            "intent_preview": (st.intent[:80] + "…") if len(st.intent) > 80 else st.intent,
            "urls": urls,
        }
        by_subtask.append(entry)
        for u in urls:
            if u not in all_urls:
                all_urls.append(u)

    return {
        "task_id": task_id,
        "seed": seed,
        "num_subtasks": len(task.subtasks),
        "by_subtask": by_subtask,
        "all_urls": all_urls,
    }


def main():
    parser = argparse.ArgumentParser(description="Get required URLs for a task_id and seed")
    parser.add_argument("--task-id", type=int, required=True, help="Task ID (1 to max_task_id)")
    parser.add_argument("--seed", type=int, default=None, help="Seed (default: variation_seed from task_id)")
    parser.add_argument("--json", action="store_true", help="Output full JSON instead of URL list")
    args = parser.parse_args()

    if args.task_id < 1 or args.task_id > TaskRegistry.max_task_id():
        print(f"Error: task_id must be between 1 and {TaskRegistry.max_task_id()}", file=sys.stderr)
        sys.exit(1)

    config = parse_task_id(args.task_id)
    seed = args.seed if args.seed is not None else config["variation_seed"]

    result = asyncio.run(get_required_urls(args.task_id, seed))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(f"# task_id={result['task_id']} seed={result['seed']} ({len(result['all_urls'])} URLs)")
    for u in result["all_urls"]:
        print(u)


if __name__ == "__main__":
    main()
