"""LiveWeb Arena - Main evaluation entry point"""

import asyncio
import os
import random
import time
from typing import Dict, List, Optional, Type

from liveweb_arena.core.browser import BrowserEngine
from liveweb_arena.core.task_manager import TaskManager
from liveweb_arena.core.agent_policy import AgentPolicy
from liveweb_arena.core.agent_loop import AgentLoop
from liveweb_arena.core.parser import AnswerParser
from liveweb_arena.plugins.base import BasePlugin
from liveweb_arena.plugins.weather import WeatherPlugin
from liveweb_arena.plugins.taostats import TaostatsPlugin
from liveweb_arena.plugins.stooq import StooqPlugin
from liveweb_arena.core.validators.llm_validator import validate_answers_with_llm
from liveweb_arena.utils.llm_client import LLMClient
from liveweb_arena.utils.logger import log


class Actor:
    """
    LiveWeb Arena evaluation actor.

    Evaluates LLM browser agents on real-world web interaction tasks.
    Features:
    - Dynamic task generation using seeds for reproducibility
    - Real-time API validation against live websites
    - Plugin-based architecture for extensible task types
    - LLM-based flexible answer validation
    """

    # Plugin registry
    PLUGINS: Dict[str, Type[BasePlugin]] = {
        "weather": WeatherPlugin,
        "taostats": TaostatsPlugin,
        "stooq": StooqPlugin,
    }

    def __init__(self, api_key: str = None):
        """
        Initialize Actor.

        Args:
            api_key: API key for LLM service. Falls back to CHUTES_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("CHUTES_API_KEY")
        self.browser: Optional[BrowserEngine] = None
        self.task_manager = TaskManager(self.PLUGINS)
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._lock = asyncio.Lock()

    async def evaluate(
        self,
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        seed: Optional[int] = None,
        num_subtasks: int = 2,
        plugins: Optional[List[str]] = None,
        max_steps: int = 30,
        timeout: int = 3600,
        temperature: float = 0.7,
        max_concurrency: int = 2,
        validation_model: Optional[str] = None,
        template_name: Optional[str] = None,
        metric: Optional[str] = None,
    ) -> dict:
        """
        Run a single evaluation.

        Args:
            model: Model name for the LLM agent
            base_url: OpenAI-compatible API base URL
            api_key: Override API key for this evaluation
            seed: Deterministic task generation seed (random if None)
            num_subtasks: Number of sub-tasks (1-4)
            plugins: Explicit plugin list; None = random selection
            max_steps: Max browser interaction steps
            timeout: Total wall-clock budget in seconds
            temperature: LLM temperature
            max_concurrency: Container-local concurrency limit
            validation_model: Model for answer validation (default: same as model)
            template_name: Optional specific template to use
            metric: Optional specific metric/type to query

        Returns:
            Evaluation result dict with scores and metadata
        """
        start_time = time.time()

        # Generate seed if not provided
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        # Allow per-call API key override
        current_api_key = api_key or self.api_key

        # Initialize semaphore for concurrency control
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max_concurrency)

        async with self._semaphore:
            try:
                result = await self._run_evaluation(
                    model=model,
                    base_url=base_url,
                    api_key=current_api_key,
                    seed=seed,
                    num_subtasks=num_subtasks,
                    plugins=plugins,
                    max_steps=max_steps,
                    timeout=timeout,
                    temperature=temperature,
                    validation_model=validation_model,
                    template_name=template_name,
                    metric=metric,
                )
            except Exception as e:
                import traceback
                result = {
                    "task_name": f"liveweb_arena:{num_subtasks}tasks",
                    "score": 0.0,
                    "success": False,
                    "time_taken": time.time() - start_time,
                    "extra": {
                        "seed": seed,
                        "num_subtasks": num_subtasks,
                        "conversation": [],
                    },
                    "error": f"{type(e).__name__}: {str(e)}",
                    "error_trace": traceback.format_exc(),
                }

        result["time_taken"] = time.time() - start_time
        return result

    async def _run_evaluation(
        self,
        model: str,
        base_url: str,
        api_key: str,
        seed: int,
        num_subtasks: int,
        plugins: Optional[List[str]],
        max_steps: int,
        timeout: int,
        temperature: float,
        validation_model: Optional[str] = None,
        template_name: Optional[str] = None,
        metric: Optional[str] = None,
    ) -> dict:
        """Internal evaluation logic"""
        await self._ensure_browser()

        task = await self.task_manager.generate_composite_task(
            seed=seed,
            num_subtasks=num_subtasks,
            plugin_names=plugins,
            template_name=template_name,
            metric=metric,
        )
        log("Actor", f"Generated {len(task.subtasks)} subtasks, seed={seed}")

        # Create isolated browser session
        session = await self.browser.new_session()

        try:
            llm_client = LLMClient(base_url=base_url, api_key=api_key)
            agent_loop = AgentLoop(
                session=session,
                llm_client=llm_client,
                policy=AgentPolicy(),
                max_steps=max_steps,
            )

            # Fetch ground truths BEFORE agent starts (same time point as AI query)
            ground_truths = await self._fetch_ground_truths_with_retry(task.subtasks)
            log("Actor", f"Ground truths fetched: {list(ground_truths.keys())}")

            # Track failure reasons
            failure_reason = None
            agent_timeout = False

            try:
                trajectory, final_answer, usage = await asyncio.wait_for(
                    agent_loop.run(task=task, model=model, temperature=temperature, seed=seed),
                    timeout=timeout,
                )
                # Check for loop detection or max steps
                if agent_loop.is_loop_detected():
                    failure_reason = "loop_detected"
                    log("Actor", "Agent stuck in loop - marking as failed", force=True)
                elif agent_loop.is_max_steps_reached():
                    failure_reason = "max_steps_reached"
                    log("Actor", "Max steps reached without completion - marking as failed", force=True)
            except asyncio.TimeoutError:
                agent_timeout = True
                failure_reason = "agent_timeout"
                log("Actor", f"Agent timeout after {timeout}s", force=True)
                trajectory = agent_loop.get_trajectory()
                final_answer = agent_loop.get_final_answer()
                usage = agent_loop.get_usage()

            # Parse answers
            parser = AnswerParser()
            parsed_answers = parser.parse_answers(final_answer, num_subtasks)
            output_format = parser.get_output_format(final_answer)
            validation_rules = {}
            for subtask in task.subtasks:
                plugin = self.task_manager.get_plugin(subtask.plugin_name)
                validation_rules[subtask.answer_tag] = plugin.get_validation_rules(
                    subtask.validation_info
                )

            # Use LLM to validate answers
            # Default validation model: openai/gpt-oss-120b-TEE (fast and reliable)
            actual_validation_model = validation_model or "openai/gpt-oss-120b-TEE"
            answer_validations = await validate_answers_with_llm(
                llm_client=llm_client,
                subtasks=task.subtasks,
                answers=parsed_answers,
                ground_truths=ground_truths,
                validation_rules=validation_rules,
                model=model,
                validation_model=actual_validation_model,
            )

            # Calculate overall score
            if failure_reason:
                # Agent failed (loop, max_steps, timeout) - score is 0
                total_score = 0.0
                success = False
            elif answer_validations:
                total_score = sum(v["score"] for v in answer_validations) / len(answer_validations)
                success = total_score >= 0.8
            else:
                total_score = 0.0
                success = False

            # Get final URL
            final_url = None
            if trajectory:
                final_url = trajectory[-1].observation.url

            # Build conversation history
            conversation = self._build_conversation(task, trajectory)

            # Build result with answer details array in metadata
            return {
                "task_name": f"liveweb_arena:{num_subtasks}tasks",
                "score": total_score,
                "success": success,
                "time_taken": 0.0,  # Will be set by caller
                "extra": {
                    "seed": seed,
                    "num_subtasks": num_subtasks,
                    "final_url": final_url,
                    "output_format": output_format,
                    "usage": usage,
                    "answer_details": answer_validations,
                    "conversation": conversation,
                    "failure_reason": failure_reason,
                },
            }

        finally:
            # Always close the session
            await session.close()

    async def _fetch_ground_truths_with_retry(
        self,
        subtasks: list,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> dict:
        """
        Fetch ground truths for all subtasks with retry mechanism.

        Args:
            subtasks: List of SubTask objects
            max_retries: Maximum number of retry attempts per subtask
            retry_delay: Delay between retries in seconds

        Returns:
            Dict mapping answer_tag to ground truth value
        """
        ground_truths = {}

        for subtask in subtasks:
            plugin = self.task_manager.get_plugin(subtask.plugin_name)
            last_error = None

            for attempt in range(max_retries):
                try:
                    gt_result = await plugin.get_ground_truth(subtask.validation_info)
                    # Treat None as failure - ground truth must be available
                    if gt_result is not None:
                        ground_truths[subtask.answer_tag] = gt_result
                        break
                    else:
                        last_error = Exception("Ground truth returned None")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                    continue
            else:
                ground_truths[subtask.answer_tag] = None
                log("Actor", f"Ground truth fetch failed for {subtask.answer_tag}: {last_error}", force=True)

        return ground_truths

    async def _ensure_browser(self):
        """Ensure browser is started (lazy initialization)"""
        async with self._lock:
            if self.browser is None:
                self.browser = BrowserEngine(headless=True)
                await self.browser.start()

    async def shutdown(self):
        """Shutdown browser and cleanup resources"""
        if self.browser:
            await self.browser.stop()
            self.browser = None

    def _build_conversation(
        self,
        task: "CompositeTask",
        trajectory: List["TrajectoryStep"],
    ) -> List[dict]:
        """
        Build conversation history from task and trajectory.

        Uses standard conversation format:
        - system: Rules and output format (not the question itself)
        - user: The actual task/question, and environment observations
        - assistant: Agent's thought and action

        Args:
            task: The composite task
            trajectory: List of trajectory steps

        Returns:
            List of conversation turns with role, content, and metadata
        """
        from liveweb_arena.core.models import CompositeTask, TrajectoryStep

        conversation = []

        # System message: only rules and output format
        system_content = """You are a browser automation agent. Navigate web pages to complete tasks.

## Output Requirements

When you have completed all tasks, use the "stop" action with your answers in JSON format:

```json
{"answers": {"answer1": "...", "answer2": "..."}}
```

Each answer should be a concise, direct response to the corresponding task."""

        conversation.append({
            "role": "system",
            "content": system_content,
            "metadata": {
                "type": "instructions",
            }
        })

        # User message: the actual task questions
        questions = []
        for i, subtask in enumerate(task.subtasks, 1):
            questions.append(f"{i}. {subtask.intent}\n   Answer tag: {subtask.answer_tag}")

        user_content = "## Tasks to Complete\n\n" + "\n\n".join(questions)

        conversation.append({
            "role": "user",
            "content": user_content,
            "metadata": {
                "type": "task_questions",
                "num_subtasks": len(task.subtasks),
            }
        })

        # Alternating user (environment) and assistant (agent) turns
        for step in trajectory:
            # User turn: environment observation
            obs_content = (
                f"URL: {step.observation.url}\n"
                f"Title: {step.observation.title}\n"
                f"Page Content:\n{step.observation.accessibility_tree[:2000]}"
            )
            if len(step.observation.accessibility_tree) > 2000:
                obs_content += "\n... (truncated)"

            conversation.append({
                "role": "user",
                "content": obs_content,
                "metadata": {
                    "type": "environment",
                    "step": step.step_num,
                    "url": step.observation.url,
                }
            })

            # Assistant turn: action only, thought in metadata
            if step.action:
                action_content = f"{step.action.action_type} {step.action.params}" if step.action.params else step.action.action_type
            else:
                action_content = "(no action)"

            conversation.append({
                "role": "assistant",
                "content": action_content,
                "metadata": {
                    "type": "agent_action",
                    "step": step.step_num,
                    "thought": step.thought,
                    "action_type": step.action.action_type if step.action else None,
                    "action_result": step.action_result,
                }
            })

        return conversation
