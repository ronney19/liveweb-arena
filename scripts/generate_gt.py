#!/usr/bin/env python3
"""
Generate correct ground truth (GT) for a specific plugin template.

Useful for testing templates, debugging validation, or producing reference
answers without running the browser agent. For templates that use the GT
collector (PAGE_ONLY/HYBRID), the script prefills the collector by
fetching the required page API data before calling get_ground_truth.

Usage:
    # By plugin and template name (template = full registry name)
    python scripts/generate_gt.py --plugin taostats --template taostats_subnet_info --seed 42

    # Template can be short name (plugin prefix added if needed)
    python scripts/generate_gt.py --plugin coingecko --template price --seed 12345

    # List available templates for a plugin
    python scripts/generate_gt.py --plugin taostats --list-templates

    # JSON output
    python scripts/generate_gt.py --plugin taostats --template taostats_subnet_info --seed 42 --json

Environment:
    Optional API keys (see .env.example): COINGECKO_DEMO_API_KEY, COINGECKO_API_KEY,
    TAOSTATS_API_KEY, etc.
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

# Add project root for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from liveweb_arena.plugins import get_all_plugins
from liveweb_arena.core.task_manager import TaskManager

# Plugins excluded from this script (empty = use all; add plugin names to disable).
EXCLUDED_PLUGINS: set = set()


def _get_plugins_for_script():
    """Return plugin dict with EXCLUDED_PLUGINS removed (script does not use them)."""
    all_plugins = get_all_plugins()
    return {k: v for k, v in all_plugins.items() if k not in EXCLUDED_PLUGINS}
from liveweb_arena.core.gt_collector import GTCollector, set_current_gt_collector
from liveweb_arena.core.validators.base import get_registered_templates, get_template
from liveweb_arena.core.ground_truth_trigger import GroundTruthResult


def _required_urls_for_gt(plugin_name: str, template_name: str, validation_info: Dict[str, Any]) -> List[str]:
    """
    Return list of URLs that must be "visited" (API data fetched) to compute GT
    for this template. Used to prefill the GT collector in standalone mode.
    """
    urls: List[str] = []
    vi = validation_info

    if plugin_name == "taostats":
        if "subnet_id" in vi:
            subnet_id = vi["subnet_id"]
            urls.append(f"https://taostats.io/subnets/{subnet_id}")
        elif "subnet_ids" in vi:
            for sid in vi["subnet_ids"]:
                urls.append(f"https://taostats.io/subnets/{sid}")
        elif "netuids" in vi:
            for nid in vi["netuids"]:
                urls.append(f"https://taostats.io/subnets/{nid}")
        else:
            urls.append("https://taostats.io/subnets")
        # Match get_required_urls: include list page and homepage when we have detail pages
        if "subnet_id" in vi or "subnet_ids" in vi or "netuids" in vi:
            urls.append("https://taostats.io")
            urls.append("https://taostats.io/subnets")
    elif plugin_name == "coingecko":
        if "coin_id" in vi:
            urls.append(f"https://www.coingecko.com/en/coins/{vi['coin_id']}")
        elif "coin1_id" in vi and "coin2_id" in vi:
            urls.append(f"https://www.coingecko.com/en/coins/{vi['coin1_id']}")
            urls.append(f"https://www.coingecko.com/en/coins/{vi['coin2_id']}")
        elif "coin_ids" in vi:
            for cid in vi["coin_ids"]:
                urls.append(f"https://www.coingecko.com/en/coins/{cid}")
        else:
            urls.append("https://www.coingecko.com/")
    elif plugin_name == "stooq":
        # Use same URL format as get_required_urls / templates (q/?s=) so training dataset matches required URLs
        if "symbol" in vi:
            urls.append(f"https://stooq.com/q/?s={vi['symbol']}")
        elif "symbols" in vi:
            for s in vi["symbols"]:
                urls.append(f"https://stooq.com/q/?s={s}")
        elif "instruments" in vi:
            # stooq_ranking, volatility, etc.: list of (symbol, name) tuples
            for item in vi["instruments"]:
                sym = item[0] if isinstance(item, (list, tuple)) else item
                urls.append(f"https://stooq.com/q/?s={sym}")
        elif "group1_instruments" in vi or "group2_instruments" in vi:
            for key in ("group1_instruments", "group2_instruments"):
                for item in vi.get(key, []):
                    sym = item[0] if isinstance(item, (list, tuple)) else item
                    urls.append(f"https://stooq.com/q/?s={sym}")
        else:
            urls.append("https://stooq.com/")
    elif plugin_name == "hackernews":
        # category_comparison needs Ask HN and Show HN category pages for GT
        cat1 = vi.get("category1_slug")
        cat2 = vi.get("category2_slug")
        if cat1 and cat2:
            urls.append(f"https://news.ycombinator.com/{cat1}")
            urls.append(f"https://news.ycombinator.com/{cat2}")
        else:
            urls.append("https://news.ycombinator.com/")
    elif plugin_name == "openlibrary":
        # openlibrary_book_stats: need search page for book (GT uses works or work detail from collector)
        if "search_query" in vi:
            q = vi["search_query"].replace(" ", "+")
            urls.append(f"https://openlibrary.org/search?q={q}")
        # openlibrary_subject_multi_condition: need search page for subject sorted by editions
        elif "subject" in vi:
            display = vi["subject"].replace("_", " ")
            q = display.replace(" ", "+")
            urls.append(f"https://openlibrary.org/search?q={q}&sort=editions")
        else:
            urls.append("https://openlibrary.org/")
    elif plugin_name == "weather":
        # Use same URL format as get_required_urls (quote + space to +) so training dataset matches required URLs
        from urllib.parse import quote
        if "location" in vi:
            loc = str(vi["location"]).replace(" ", "+")
            urls.append(f"https://wttr.in/{quote(loc, safe='+')}")
        elif "city1_query" in vi and "city2_query" in vi:
            urls.append(f"https://wttr.in/{quote(str(vi['city1_query']).replace(' ', '+'), safe='+')}")
            urls.append(f"https://wttr.in/{quote(str(vi['city2_query']).replace(' ', '+'), safe='+')}")
    elif plugin_name == "openmeteo":
        def _openmeteo_docs_url(coord_key: str) -> str:
            if not coord_key:
                return ""
            parts = str(coord_key).strip().split(",")
            if len(parts) != 2:
                return ""
            try:
                lat, lon = float(parts[0]), float(parts[1])
                return f"https://open-meteo.com/en/docs?latitude={lat}&longitude={lon}"
            except ValueError:
                return ""
        if "coord_key" in vi:
            u = _openmeteo_docs_url(vi["coord_key"])
            if u:
                urls.append(u)
        elif "city1_coord_key" in vi and "city2_coord_key" in vi:
            u1 = _openmeteo_docs_url(vi["city1_coord_key"])
            u2 = _openmeteo_docs_url(vi["city2_coord_key"])
            if u1:
                urls.append(u1)
            if u2:
                urls.append(u2)
        else:
            urls.append("https://open-meteo.com/en/docs")
    elif plugin_name == "hybrid":
        def _hybrid_asset_url(asset: Any) -> Optional[str]:
            if not isinstance(asset, dict):
                return None
            src = asset.get("source", "")
            aid = asset.get("asset_id", "")
            sym = asset.get("symbol") or aid
            if src == "coingecko" and aid:
                return f"https://www.coingecko.com/en/coins/{aid}"
            if src == "stooq" and sym:
                return f"https://stooq.com/q/?s={sym}"
            return None

        # hybrid_top_performer, portfolio, etc.: list of assets
        if "assets" in vi:
            for a in vi["assets"]:
                u = _hybrid_asset_url(a)
                if u:
                    urls.append(u)
        # hybrid_conditional_branch: condition_asset + positive/negative/neutral_target
        for key in ("condition_asset", "positive_target", "negative_target", "neutral_target"):
            u = _hybrid_asset_url(vi.get(key))
            if u:
                urls.append(u)
        # hybrid_chained_decision: level1_asset, level2_asset, targets (dict of branch -> asset)
        for key in ("level1_asset", "level2_asset"):
            u = _hybrid_asset_url(vi.get(key))
            if u:
                urls.append(u)
        if "targets" in vi and isinstance(vi["targets"], dict):
            for branch_asset in vi["targets"].values():
                u = _hybrid_asset_url(branch_asset)
                if u:
                    urls.append(u)
        for domain_key in ("coingecko_coin_ids", "stooq_symbols", "coin_ids", "symbols"):
            if domain_key in vi:
                for item in (vi[domain_key] if isinstance(vi[domain_key], list) else [vi[domain_key]]):
                    if "coin" in domain_key or "coingecko" in domain_key:
                        urls.append(f"https://www.coingecko.com/en/coins/{item}")
                    else:
                        urls.append(f"https://stooq.com/q/?s={item}")
        if not urls:
            urls.append("https://www.coingecko.com/")
            urls.append("https://stooq.com/")

    return urls


def _normalize_template_arg(plugin_name: str, template_arg: str) -> str:
    """
    Resolve template name for plugin.
    Returns the full registry name (e.g. taostats_subnet_info).
    """
    # If it already contains underscore and matches a registered template, use it
    registered = get_registered_templates()
    if template_arg in registered:
        cls = registered[template_arg]
        if hasattr(cls, "get_cache_source") and cls.get_cache_source() == plugin_name:
            return template_arg
    # Try plugin_prefix + template_arg (e.g. taostats_subnet_info)
    prefixed = f"{plugin_name}_{template_arg}"
    if prefixed in registered:
        return prefixed
    if template_arg in registered:
        return template_arg
    return template_arg  # Let task_manager raise if invalid


def _format_exception(e: BaseException) -> str:
    """Format exception for error messages so debugging is easier (type + message)."""
    msg = str(e).strip()
    if not msg:
        msg = repr(e)
    return f"{type(e).__name__}: {msg}"


def _templates_for_plugin(plugin_name: str) -> list:
    """Return list of (template_name, template_cls) for the plugin."""
    registered = get_registered_templates()
    result = []
    for name, cls in registered.items():
        if hasattr(cls, "get_cache_source") and cls.get_cache_source() == plugin_name:
            result.append((name, cls))
    return sorted(result, key=lambda x: x[0])


async def generate_gt(
    plugin_name: str,
    template_name: str,
    seed: int,
    debug: bool = False,
) -> dict:
    """
    Generate one task for the given plugin/template/seed and compute its ground truth.

    Returns:
        dict with keys: question, ground_truth, success, error, validation_info (summary)
    """
    plugins = _get_plugins_for_script()
    if plugin_name in EXCLUDED_PLUGINS:
        return {
            "success": False,
            "error": f"Plugin '{plugin_name}' is excluded from this script. Available: {sorted(plugins.keys())}",
            "question": None,
            "ground_truth": None,
        }
    if plugin_name not in plugins:
        return {
            "success": False,
            "error": f"Unknown plugin: {plugin_name}. Available: {sorted(plugins.keys())}",
            "question": None,
            "ground_truth": None,
        }

    template_name = _normalize_template_arg(plugin_name, template_name)
    if template_name not in get_registered_templates():
        return {
            "success": False,
            "error": f"Unknown template: {template_name}. Use --list-templates for {plugin_name}.",
            "question": None,
            "ground_truth": None,
        }

    task_manager = TaskManager(plugins)
    plugin = task_manager.get_plugin(plugin_name)
    # For coingecko_rank, retry with other seeds when API doesn't provide rank (e.g. Maker on Demo)
    max_seed_tries = 5 if (plugin_name == "coingecko" and template_name == "coingecko_rank") else 1
    last_result = None
    last_subtask = None

    for try_index in range(max_seed_tries):
        try_seed = seed + try_index
        try:
            task = await task_manager.generate_composite_task(
                seed=try_seed,
                num_subtasks=1,
                templates=[(plugin_name, template_name, None)],
            )
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "question": None,
                "ground_truth": None,
            }

        subtask = task.subtasks[0]
        last_subtask = subtask
        collector = GTCollector(subtasks=task.subtasks, task_manager=task_manager)
        set_current_gt_collector(collector)

        try:
            urls = _required_urls_for_gt(plugin_name, template_name, subtask.validation_info)
            for url in urls:
                try:
                    api_data = await plugin.fetch_api_data(url)
                    if api_data:
                        await collector.on_page_visit(url, "", api_data=api_data)
                except Exception as e:
                    if debug:
                        traceback.print_exc(file=sys.stderr)
                    set_current_gt_collector(None)
                    collector.cleanup()
                    err_msg = _format_exception(e)
                    return {
                        "success": False,
                        "error": f"Failed to fetch API data for {url}: {err_msg}",
                        "question": subtask.intent,
                        "ground_truth": None,
                        "validation_info_keys": list(subtask.validation_info.keys()),
                    }

            result = await plugin.get_ground_truth(subtask.validation_info)
        except Exception as e:
            if debug:
                traceback.print_exc(file=sys.stderr)
            set_current_gt_collector(None)
            collector.cleanup()
            err_msg = _format_exception(e)
            return {
                "success": False,
                "error": err_msg,
                "question": subtask.intent,
                "ground_truth": None,
                "validation_info_keys": list(subtask.validation_info.keys()),
            }
        finally:
            set_current_gt_collector(None)
            collector.cleanup()

        if isinstance(result, GroundTruthResult) and result.success:
            last_result = result
            break
        last_result = result
        # Retry only when failure is due to missing rank (e.g. Demo API for some coins)
        is_rank_unavailable = (
            isinstance(result, GroundTruthResult)
            and result.error
            and ("market cap rank" in result.error.lower() or "Try a different seed" in result.error)
        )
        if not is_rank_unavailable:
            break

    result = last_result
    subtask = last_subtask

    # Print URLs that correspond to the task we're returning (important when retries changed the subtask)
    urls_returned = _required_urls_for_gt(plugin_name, template_name, subtask.validation_info)
    print(f"Required URLs for the task: {urls_returned}")

    if isinstance(result, GroundTruthResult):
        return {
            "success": result.success,
            "error": result.error if not result.success else None,
            "question": subtask.intent,
            "ground_truth": result.value if result.success else None,
            "validation_info_keys": list(subtask.validation_info.keys()),
        }
    else:
        return {
            "success": True,
            "error": None,
            "question": subtask.intent,
            "ground_truth": result,
            "validation_info_keys": list(subtask.validation_info.keys()),
        }


async def main():
    parser = argparse.ArgumentParser(
        description="Generate correct ground truth for a specific plugin template"
    )
    parser.add_argument(
        "--plugin", "-p",
        type=str,
        required=True,
        help="Plugin name (e.g. taostats, coingecko, stooq, hackernews, hybrid)",
    )
    parser.add_argument(
        "--template", "-t",
        type=str,
        default=None,
        help="Template name (e.g. taostats_subnet_info, coingecko_price). Use --list-templates to see options.",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed for reproducible question generation (default: 42)",
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List available templates for the plugin and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="On failure, print full traceback to stderr for easier debugging",
    )

    args = parser.parse_args()

    # Ensure templates are loaded
    get_all_plugins()

    if args.plugin in EXCLUDED_PLUGINS:
        print(f"Error: Plugin '{args.plugin}' is excluded from this script.", file=sys.stderr)
        print(f"Available plugins: {sorted(_get_plugins_for_script().keys())}", file=sys.stderr)
        sys.exit(1)

    if args.list_templates:
        templates = _templates_for_plugin(args.plugin)
        if not templates:
            print(f"No templates found for plugin: {args.plugin}", file=sys.stderr)
            sys.exit(1)
        print(f"Templates for plugin '{args.plugin}':")
        for name, _ in templates:
            print(f"  {name}")
        sys.exit(0)

    if not args.template:
        print("Error: --template is required (or use --list-templates)", file=sys.stderr)
        sys.exit(1)

    result = await generate_gt(
        plugin_name=args.plugin,
        template_name=args.template,
        seed=args.seed,
        debug=args.debug,
    )

    if args.json:
        # Serialize for JSON (e.g. non-str GT)
        out = {
            "success": result["success"],
            "error": result.get("error"),
            "question": result.get("question"),
            "ground_truth": result.get("ground_truth"),
            "seed": args.seed,
            "plugin": args.plugin,
            "template": args.template,
        }
        if "validation_info_keys" in result:
            out["validation_info_keys"] = result["validation_info_keys"]
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        sys.exit(0 if result["success"] else 1)

    # Human-readable output
    if not result["success"]:
        print(f"Error: {result['error']}", file=sys.stderr)
        if result.get("question"):
            print(f"Question: {result['question']}")
        sys.exit(1)

    print("Question:")
    print(result["question"])
    print()
    print("Ground truth:")
    print(result["ground_truth"])
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
