#!/usr/bin/env python3
"""
LiveWeb Arena - Standalone Evaluation Script

Usage:
    python eval.py [options]

Examples:
    # Basic evaluation (random)
    python eval.py --model "zai-org/GLM-4.7-TEE" --seed 42

    # With task_id (deterministic, reproducible question type)
    python eval.py --model "openai/gpt-oss-120b-TEE" --task-id 50001

    # Show task registry info
    python eval.py --show-registry

Environment:
    Copy .env.example to .env and configure your API keys.
    The script automatically loads .env on startup.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from env import Actor
from liveweb_arena.utils.logger import set_verbose
from liveweb_arena.core.task_registry import TaskRegistry, parse_task_id, max_task_id


async def main():
    parser = argparse.ArgumentParser(
        description="LiveWeb Arena - Real-time web evaluation for LLM browser agents"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="zai-org/GLM-4.7",
        help="LLM model name (default: zai-org/GLM-4.7)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://llm.chutes.ai/v1",
        help="OpenAI-compatible API base URL (default: https://llm.chutes.ai/v1)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key (default: from API_KEY env var)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility (default: random)",
    )
    parser.add_argument(
        "--num-tasks",
        type=int,
        default=1,
        help="Number of sub-tasks (1-4, default: 1)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum browser interaction steps (default: auto based on task complexity)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Total timeout in seconds (default: 3600)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM temperature (default: 0.0)",
    )
    parser.add_argument(
        "--validation-model",
        type=str,
        default="openai/gpt-oss-120b-TEE",
        help="Model for answer validation (default: openai/gpt-oss-120b-TEE)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for results (default: eval/yyyy_mm_dd_hh_mm_ss.json)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output",
    )
    parser.add_argument(
        "--templates",
        type=str,
        nargs="+",
        default=None,
        help="Templates to use (e.g., weather/multi_day stooq/stooq_price)",
    )
    parser.add_argument(
        "--task-id",
        type=int,
        default=None,
        help="Task ID for deterministic question type (1 to max, see --show-registry)",
    )
    parser.add_argument(
        "--show-registry",
        action="store_true",
        help="Show task registry info and exit",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live mode (no caching, real-time web requests)",
    )

    args = parser.parse_args()

    # Handle --show-registry
    if args.show_registry:
        TaskRegistry.print_info()
        sys.exit(0)

    # Validate task_id range
    if args.task_id is not None:
        if args.task_id < 1 or args.task_id > max_task_id():
            print(f"Error: task_id must be between 1 and {max_task_id()}")
            sys.exit(1)

    # Set verbose mode globally (on by default, --quiet disables)
    verbose = not args.quiet
    set_verbose(verbose)

    # Get API key
    api_key = args.api_key or os.getenv("API_KEY")
    if not api_key:
        print("Error: API key required. Set API_KEY or use --api-key")
        sys.exit(1)

    # Parse templates from "plugin/template_name[/variant]" format
    def parse_templates(template_strs):
        """Parse 'plugin/template_name' or 'plugin/template_name/variant' strings."""
        if not template_strs:
            return None
        result = []
        for t in template_strs:
            parts = t.split("/")
            if len(parts) == 2:
                plugin, name = parts
                result.append((plugin, name, None))
            elif len(parts) == 3:
                plugin, name, variant = parts
                result.append((plugin, name, int(variant)))
            else:
                raise ValueError(f"Invalid template format: {t}. Use 'plugin/template_name[/variant]'")
        return result

    # Prepare config based on task_id and/or seed
    if args.task_id is not None:
        task_config = parse_task_id(args.task_id)
        seed = args.seed if args.seed is not None else task_config["variation_seed"]
        num_tasks = args.num_tasks if args.num_tasks != 1 else task_config["num_tasks"]
        templates = task_config["templates"]

        if verbose:
            print(f"Task ID: {args.task_id}")
            print(f"  Templates: {templates}")
            print(f"  Num tasks: {num_tasks}")
            print(f"  Seed: {seed}")
    else:
        seed = args.seed
        num_tasks = args.num_tasks
        templates = parse_templates(args.templates)

    if verbose:
        config_parts = [f"model={args.model}"]
        if args.task_id:
            config_parts.append(f"task_id={args.task_id}")
        config_parts.append(f"seed={seed or 'random'}")
        config_parts.append(f"tasks={num_tasks}")
        if templates:
            config_parts.append(f"templates={templates}")
        print(f"Config: {', '.join(config_parts)}")

    # Determine cache mode from --live flag
    use_cache = not args.live

    # Initialize actor
    actor = Actor(api_key=api_key, use_cache=use_cache)

    if not use_cache:
        print("Mode: LIVE (real-time web requests, no caching)")
        print("-" * 50)

    try:
        print("Starting evaluation...")
        print("-" * 50)

        result = await actor.evaluate(
            model=args.model,
            base_url=args.base_url,
            seed=seed,
            num_subtasks=num_tasks,
            templates=templates,
            max_steps=args.max_steps,
            timeout=args.timeout,
            temperature=args.temperature,
            validation_model=args.validation_model,
            task_id=args.task_id,
        )

        # Print results
        print()
        print("=" * 50)
        print("EVALUATION RESULT")
        print("=" * 50)
        print(f"Task: {result['task_name']}")
        print(f"Score: {result['score']:.2f}")
        print(f"Success: {result['success']}")
        print(f"Time: {result['time_taken']:.2f}s")

        if result.get("error"):
            print(f"Error: {result['error']}")

        # Print answer details
        extra = result.get("extra", {})
        answer_details = extra.get("answer_details", [])
        if answer_details:
            print()
            print("--- Answer Details ---")
            for detail in answer_details:
                print(f"\nQuestion: {detail['question']}")
                print(f"Expected: {detail['expected']}")
                print(f"Actual: {detail['actual']}")
                print(f"Score: {detail['score']:.2f}")
                print(f"Reasoning: {detail['reasoning']}")

        # Print usage
        usage = extra.get("usage")
        if usage:
            print()
            print("--- Token Usage ---")
            print(f"Prompt: {usage.get('prompt_tokens', 0)}")
            print(f"Completion: {usage.get('completion_tokens', 0)}")
            print(f"Total: {usage.get('total_tokens', 0)}")

        # Print cache stats
        cache_stats = extra.get("cache_stats")
        if cache_stats:
            print()
            print("--- Cache Stats ---")
            print(f"Hits: {cache_stats.get('hits', 0)}")
            print(f"Misses: {cache_stats.get('misses', 0)}")
            print(f"Blocked: {cache_stats.get('blocked', 0)}")

        # Save to file
        if args.output:
            output_path = Path(args.output)
        else:
            eval_dir = Path(__file__).parent / "eval"
            eval_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            output_path = eval_dir / f"{timestamp}.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to: {output_path}")

        return 0 if result["success"] else 1

    except KeyboardInterrupt:
        print("\nEvaluation interrupted by user")
        return 130

    except Exception as e:
        import traceback
        print(f"\nError: {e}")
        if verbose:
            traceback.print_exc()
        return 1

    finally:
        await actor.shutdown()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    os._exit(exit_code)
