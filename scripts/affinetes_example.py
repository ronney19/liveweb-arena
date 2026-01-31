#!/usr/bin/env python3
"""
LiveWeb Arena evaluation via affinetes.

Demonstrates how to build, load, and interact with the LiveWeb Arena
environment through the affinetes container framework.

Usage:
    python scripts/affinetes_example.py [options]

Examples:
    # Run with pre-built image
    python scripts/affinetes_example.py --image liveweb-arena:latest

    # Build and run
    python scripts/affinetes_example.py --build

    # Custom model and seed
    python scripts/affinetes_example.py --image liveweb-arena:latest \
        --model "openai/gpt-oss-120b-TEE" --seed 42

    # Specific task
    python scripts/affinetes_example.py --image liveweb-arena:latest \
        --task-id 30001

Environment:
    API_KEY: Required. API key for LLM service.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

try:
    import affinetes as af
except ImportError:
    print("Error: affinetes is not installed.")
    print("Install it with: pip install git+https://github.com/AffineFoundation/affinetes.git@main")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = "liveweb-arena:latest"
CONTAINER_CACHE_DIR = "/var/lib/liveweb-arena/cache"


async def main():
    parser = argparse.ArgumentParser(description="LiveWeb Arena evaluation via affinetes")
    parser.add_argument("--image", type=str, default=DEFAULT_IMAGE, help=f"Docker image (default: {DEFAULT_IMAGE})")
    parser.add_argument("--build", action="store_true", help="Build image before running")
    parser.add_argument("--model", type=str, default="zai-org/GLM-4.7", help="LLM model name")
    parser.add_argument("--base-url", type=str, default="https://llm.chutes.ai/v1", help="LLM API base URL")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--task-id", type=int, default=None, help="Deterministic task ID")
    parser.add_argument("--num-tasks", type=int, default=1, help="Number of sub-tasks (1-4)")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout in seconds")
    parser.add_argument("--pull", action="store_true", help="Pull image from registry")
    parser.add_argument("--force-recreate", action="store_true", help="Force recreate container")
    args = parser.parse_args()

    # Validate API key
    api_key = os.getenv("API_KEY")
    if not api_key:
        print("Error: API_KEY environment variable not set.")
        print("Set it with: export API_KEY='your-key'")
        sys.exit(1)

    # Step 1: Build if requested
    image = args.image
    if args.build:
        print(f"Building image from {PROJECT_ROOT}...")
        image = af.build_image_from_env(
            env_path=str(PROJECT_ROOT),
            image_tag=image,
        )
        print(f"Image built: {image}")

    # Step 2: Load environment
    # Mount local ./cache into container so cached pages are shared and persist.
    host_cache = str(PROJECT_ROOT / "cache")
    print(f"Loading environment from image: {image}")
    print(f"Cache mount: {host_cache} -> {CONTAINER_CACHE_DIR}")
    env = af.load_env(
        image=image,
        mode="docker",
        env_vars={
            "API_KEY": api_key,
            "LIVEWEB_CACHE_DIR": CONTAINER_CACHE_DIR,
        },
        pull=args.pull,
        force_recreate=args.force_recreate,
        volumes={host_cache: {"bind": CONTAINER_CACHE_DIR, "mode": "rw"}},
    )
    print("Environment loaded (container started with HTTP server)")

    try:
        # Step 3: List available methods
        print("\nAvailable methods:")
        await env.list_methods()

        # Step 4: Run evaluation
        print("\nStarting evaluation...")
        print("-" * 50)

        eval_kwargs = {
            "model": args.model,
            "base_url": args.base_url,
            "num_subtasks": args.num_tasks,
            "timeout": args.timeout,
        }
        if args.seed is not None:
            eval_kwargs["seed"] = args.seed
        if args.task_id is not None:
            eval_kwargs["task_id"] = args.task_id

        result = await env.evaluate(**eval_kwargs, _timeout=args.timeout + 60)

        # Step 5: Display results
        print("\n" + "=" * 50)
        print("EVALUATION RESULT")
        print("=" * 50)
        print(f"Task:    {result.get('task_name', 'N/A')}")
        print(f"Score:   {result.get('score', 0):.2f}")
        print(f"Success: {result.get('success', False)}")
        print(f"Time:    {result.get('time_taken', 0):.2f}s")

        if result.get("error"):
            print(f"Error:   {result['error']}")

        extra = result.get("extra", {})
        answer_details = extra.get("answer_details", [])
        if answer_details:
            print("\n--- Answer Details ---")
            for detail in answer_details:
                print(f"\n  Question: {detail.get('question', '')}")
                print(f"  Expected: {detail.get('expected', '')}")
                print(f"  Actual:   {detail.get('actual', '')}")
                print(f"  Score:    {detail.get('score', 0):.2f}")

        # Save full result
        output_dir = PROJECT_ROOT / "eval"
        output_dir.mkdir(exist_ok=True)
        from datetime import datetime
        output_path = output_dir / f"affinetes_{datetime.now().strftime('%Y_%m_%d_%H_%M_%S')}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nFull results saved to: {output_path}")

    except Exception as e:
        print(f"\nEvaluation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        await env.cleanup()
        print("Environment cleaned up.")


if __name__ == "__main__":
    asyncio.run(main())
