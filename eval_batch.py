#!/usr/bin/env python3
"""
LiveWeb Arena - Batch Evaluation Script

Evaluates multiple (task_id, seed) pairs from a JSON file.
Uses the same Actor and options as eval.py; runs each pair sequentially
and writes aggregated results to a single output file.

Usage:
    python eval_batch.py --tasks-json test_id.json --model "zai-org/GLM-4.7-TEE"

Tasks JSON format (array of objects with task_id and seed):
    [
      {"task_id": 38880007, "seed": 3894062276},
      {"task_id": 6803969, "seed": 60644206}
    ]

Optional keys per item: num_tasks, max_steps (override CLI defaults for that run).
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from env import Actor
from liveweb_arena.utils.logger import set_verbose
from liveweb_arena.core.task_registry import TaskRegistry, max_task_id


def load_tasks_json(path: str) -> list:
    """Load and validate list of {task_id, seed[, num_tasks, max_steps]} from JSON file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Tasks file not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Tasks JSON must be an array of objects")
    max_id = max_task_id()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Tasks[{i}]: must be an object, got {type(item).__name__}")
        if "task_id" not in item or "seed" not in item:
            raise ValueError(f"Tasks[{i}]: must have 'task_id' and 'seed'")
        tid, seed = item["task_id"], item["seed"]
        if not isinstance(tid, int) or not isinstance(seed, int):
            raise ValueError(f"Tasks[{i}]: task_id and seed must be integers")
        if tid < 1 or tid > max_id:
            raise ValueError(f"Tasks[{i}]: task_id must be 1..{max_id}, got {tid}")
    return data


async def main():
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(
        description="LiveWeb Arena - Batch evaluation over multiple (task_id, seed) pairs"
    )
    parser.add_argument(
        "--tasks-json",
        type=str,
        required=True,
        help="Path to JSON file with array of {task_id, seed} objects (e.g. test_id.json)",
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
        help="OpenAI-compatible API base URL",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key (default: from API_KEY env var)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Max browser steps per run (default: auto)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Timeout per run in seconds (default: 3600)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM temperature (default: 0.0)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file (default: eval/batch_yyyy_mm_dd_hh_mm_ss.json)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live mode (no caching)",
    )
    parser.add_argument(
        "--show-registry",
        action="store_true",
        help="Show task registry and exit",
    )

    args = parser.parse_args()

    if args.show_registry:
        TaskRegistry.print_info()
        sys.exit(0)

    verbose = not args.quiet
    set_verbose(verbose)

    api_key = args.api_key or os.getenv("API_KEY") or os.getenv("CHUTES_API_KEY")
    if not api_key:
        print("Error: API key required. Set API_KEY or use --api-key")
        sys.exit(1)

    try:
        tasks = load_tasks_json(args.tasks_json)
    except Exception as e:
        print(f"Error loading tasks: {e}")
        sys.exit(1)

    use_cache = not args.live
    actor = Actor(api_key=api_key, use_cache=use_cache)

    if not use_cache:
        print("Mode: LIVE (real-time web requests, no caching)")
    print(f"Batch: {len(tasks)} tasks from {args.tasks_json}")
    print(f"Model: {args.model}")
    print("-" * 50)

    runs = []
    try:
        for idx, item in enumerate(tasks):
            task_id = item["task_id"]
            seed = item["seed"]
            num_tasks = item.get("num_tasks")
            max_steps = item.get("max_steps", args.max_steps)
            print(f"[{idx + 1}/{len(tasks)}] task_id={task_id} seed={seed} ... ", end="", flush=True)
            try:
                result = await actor.evaluate(
                    model=args.model,
                    base_url=args.base_url,
                    seed=seed,
                    num_subtasks=num_tasks,
                    templates=None,
                    max_steps=max_steps,
                    timeout=args.timeout,
                    temperature=args.temperature,
                    task_id=task_id,
                )
                result["_task_id"] = task_id
                result["_seed"] = seed
                result["_index"] = idx + 1
                runs.append(result)
                status = "OK" if result["success"] else "FAIL"
                print(f"score={result['score']:.2f} {status} ({result['time_taken']:.1f}s)")
                if result.get("error") and verbose:
                    print(f"       error: {result['error'][:200]}...")
            except Exception as e:
                import traceback
                err_result = {
                    "_task_id": task_id,
                    "_seed": seed,
                    "_index": idx + 1,
                    "task_name": "liveweb_arena",
                    "score": 0.0,
                    "success": False,
                    "time_taken": 0.0,
                    "error": traceback.format_exc(),
                }
                runs.append(err_result)
                print(f"EXCEPTION: {e}")

        # Summary
        success_count = sum(1 for r in runs if r.get("success"))
        total_score = sum(r.get("score", 0.0) for r in runs)
        total_time = sum(r.get("time_taken", 0.0) for r in runs)
        n = len(runs)
        avg_score = total_score / n if n else 0.0

        print()
        print("=" * 50)
        print("BATCH SUMMARY")
        print("=" * 50)
        print(f"Total:   {n} runs")
        print(f"Success: {success_count}/{n}")
        print(f"Score:   {total_score:.2f} total, {avg_score:.2f} avg")
        print(f"Time:    {total_time:.1f}s total")

        # Output: minimal run records for JSON (strip internal keys if you want; keeping them for traceability)
        out_payload = {
            "model": args.model,
            "tasks_file": args.tasks_json,
            "summary": {
                "total": n,
                "success": success_count,
                "total_score": total_score,
                "avg_score": round(avg_score, 4),
                "total_time_sec": round(total_time, 2),
            },
            "runs": runs,
        }

        if args.output:
            output_path = Path(args.output)
        else:
            eval_dir = Path(__file__).parent / "eval"
            eval_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            output_path = eval_dir / f"batch_{timestamp}.json"

        tmp_path = output_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2, ensure_ascii=False)
        tmp_path.rename(output_path)
        print(f"\nResults saved to: {output_path}")

        return 0 if success_count == n else 1

    except KeyboardInterrupt:
        print("\nBatch interrupted by user")
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
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
