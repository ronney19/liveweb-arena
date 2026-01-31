#!/usr/bin/env python3
"""
LiveWeb Arena - Inference Mode Script

Run the browser agent on a custom question without evaluation/validation.
Useful for testing and debugging specific scenarios.

Usage:
    # Direct question
    python infer.py --question "What is the name of subnet 27 on taostats?"

    # Using template with seed
    python infer.py --plugin taostats --seed 12345

    # Using template with specific metric
    python infer.py --plugin taostats --template taostats_subnet_info --metric name --subnet 27
"""

import argparse
import asyncio
import json
import os
import sys
import time

from liveweb_arena.core.browser import BrowserEngine
from liveweb_arena.core.agent_policy import AgentPolicy
from liveweb_arena.core.agent_loop import AgentLoop
from liveweb_arena.core.parser import AnswerParser
from liveweb_arena.utils.llm_client import LLMClient
from liveweb_arena.utils.logger import set_verbose, log


async def run_inference(
    question: str,
    model: str,
    base_url: str,
    api_key: str,
    max_steps: int = 20,
    timeout: int = 180,
    temperature: float = 0.7,
    plugin_hint: str = None,
):
    """
    Run agent inference on a single question.

    Args:
        question: The question/task for the agent
        model: LLM model name
        base_url: API base URL
        api_key: API key
        max_steps: Maximum browser steps
        timeout: Timeout in seconds
        temperature: LLM temperature
        plugin_hint: Optional plugin usage hint to include

    Returns:
        Dict with answer, trajectory, and timing info
    """
    start_time = time.time()

    # Start browser
    browser = BrowserEngine(headless=True)
    await browser.start()

    try:
        session = await browser.new_session()

        try:
            llm_client = LLMClient(base_url=base_url, api_key=api_key)
            agent_loop = AgentLoop(
                session=session,
                llm_client=llm_client,
                policy=AgentPolicy(),
                max_steps=max_steps,
            )

            # Build simple task structure
            class SimpleTask:
                def __init__(self, question, plugin_hint=None):
                    self.combined_intent = f"## Task\n\n{question}\n\nProvide your answer using the stop action."
                    self.plugin_hints = {"default": plugin_hint} if plugin_hint else {}
                    self.subtasks = []

            task = SimpleTask(question, plugin_hint)

            try:
                trajectory, final_answer, usage = await asyncio.wait_for(
                    agent_loop.run(task=task, model=model, temperature=temperature, seed=None),
                    timeout=timeout,
                )
                timed_out = False
            except asyncio.TimeoutError:
                log("Infer", f"Timeout after {timeout}s", force=True)
                trajectory = agent_loop.get_trajectory()
                final_answer = agent_loop.get_final_answer()
                usage = agent_loop.get_usage()
                timed_out = True

            # Get final URL
            final_url = trajectory[-1].observation.url if trajectory else None

            return {
                "question": question,
                "answer": final_answer,
                "final_url": final_url,
                "steps": len(trajectory),
                "timed_out": timed_out,
                "time_taken": time.time() - start_time,
                "usage": usage,
                "trajectory": [
                    {
                        "step": step.step_num,
                        "url": step.observation.url,
                        "thought": step.thought,
                        "action": f"{step.action.action_type} {step.action.params}" if step.action else None,
                    }
                    for step in trajectory
                ],
            }

        finally:
            await session.close()

    finally:
        await browser.stop()


async def main():
    parser = argparse.ArgumentParser(
        description="LiveWeb Arena - Inference Mode (no validation)"
    )

    # Question source (mutually exclusive)
    question_group = parser.add_mutually_exclusive_group(required=True)
    question_group.add_argument(
        "--question", "-q",
        type=str,
        help="Direct question for the agent",
    )
    question_group.add_argument(
        "--seed",
        type=int,
        help="Generate question from template using seed",
    )

    # Template options (used with --seed)
    parser.add_argument(
        "--plugin",
        type=str,
        default="taostats",
        help="Plugin to use for template generation (default: taostats)",
    )
    parser.add_argument(
        "--template",
        type=str,
        help="Specific template name (optional, random if not specified)",
    )

    # Model options
    parser.add_argument(
        "--model",
        type=str,
        default="zai-org/GLM-4.7-TEE",
        help="LLM model name (default: zai-org/GLM-4.7-TEE)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://llm.chutes.ai/v1",
        help="API base URL (default: https://llm.chutes.ai/v1)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key (default: from API_KEY env var)",
    )

    # Execution options
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum browser steps (default: 20)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="LLM temperature (default: 0.7)",
    )

    # Output options
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show verbose output including trajectory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    args = parser.parse_args()

    set_verbose(args.verbose)

    # Get API key
    api_key = args.api_key or os.getenv("API_KEY")
    if not api_key:
        print("Error: API key required. Set API_KEY or use --api-key")
        sys.exit(1)

    # Get question
    plugin_hint = None
    if args.question:
        question = args.question
    else:
        # Generate from template
        if args.template:
            # Use specific template - import templates to trigger registration
            import liveweb_arena.plugins.taostats.templates  # noqa
            import liveweb_arena.plugins.weather.templates  # noqa
            from liveweb_arena.core.validators.base import get_registered_templates
            registered = get_registered_templates()
            if args.template not in registered:
                print(f"Error: Unknown template '{args.template}'")
                print(f"Available templates: {list(registered.keys())}")
                sys.exit(1)
            template = registered[args.template]()
            generated = template.generate(args.seed)
            question = generated.question_text
            plugin_hint = ""
        else:
            from env import Actor
            actor = Actor(api_key=api_key)

            task = await actor.task_manager.generate_composite_task(
                seed=args.seed,
                num_subtasks=1,
                plugin_names=[args.plugin],
            )

            question = task.subtasks[0].intent
            plugin_hint = task.plugin_hints.get(args.plugin, "")

        print(f"Generated question (seed={args.seed}):")
        print(f"  {question}")
        print()

    # Run inference
    print(f"Running inference...")
    print(f"  Model: {args.model}")
    print(f"  Question: {question}")
    print("-" * 50)

    result = await run_inference(
        question=question,
        model=args.model,
        base_url=args.base_url,
        api_key=api_key,
        max_steps=args.max_steps,
        timeout=args.timeout,
        temperature=args.temperature,
        plugin_hint=plugin_hint,
    )

    # Output result
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print()
        print("=" * 50)
        print("INFERENCE RESULT")
        print("=" * 50)
        print(f"Question: {result['question']}")
        print(f"Answer: {result['answer']}")
        print(f"Final URL: {result['final_url']}")
        print(f"Steps: {result['steps']}")
        print(f"Time: {result['time_taken']:.2f}s")
        print(f"Timed out: {result['timed_out']}")

        if result.get('usage'):
            print(f"Tokens: {result['usage'].get('total_tokens', 'N/A')}")

        if args.verbose and result.get('trajectory'):
            print()
            print("--- Trajectory ---")
            for step in result['trajectory']:
                print(f"\nStep {step['step']}:")
                print(f"  URL: {step['url']}")
                print(f"  Thought: {step['thought'][:100]}..." if step['thought'] and len(step['thought']) > 100 else f"  Thought: {step['thought']}")
                print(f"  Action: {step['action']}")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
