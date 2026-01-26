"""LiveWeb Arena - Main evaluation entry point"""

import asyncio
import os
import random
import time
from typing import Dict, List, Optional, Type

from liveweb_arena.core.browser import BrowserEngine
from liveweb_arena.core.task_manager import TaskManager
from liveweb_arena.core.agent_policy import AgentPolicy
from liveweb_arena.core.agent_loop import AgentLoop, BrowserFatalError
from liveweb_arena.core.parser import AnswerParser
from liveweb_arena.core.ground_truth_trigger import GroundTruthManager, FetchStrategy
from liveweb_arena.core.cache_manager import (
    get_cache_manager, EvaluationCacheContext,
)
from liveweb_arena.core.cache_adapters import get_adapter_registry
from liveweb_arena.plugins.base import BasePlugin
from liveweb_arena.plugins.weather import WeatherPlugin
from liveweb_arena.plugins.taostats import TaostatsPlugin
from liveweb_arena.plugins.stooq import StooqPlugin
from liveweb_arena.plugins.coingecko import CoinGeckoPlugin
from liveweb_arena.plugins.tmdb import TMDBPlugin
from liveweb_arena.plugins.hybrid import HybridPlugin
from liveweb_arena.plugins.hybrid.utils import set_cache_context
from liveweb_arena.plugins.coingecko.api_client import set_coingecko_cache_context
from liveweb_arena.plugins.stooq.api_client import set_stooq_cache_context
from liveweb_arena.plugins.weather.api_client import set_weather_cache_context
from liveweb_arena.plugins.tmdb.api_client import set_tmdb_cache_context
from liveweb_arena.core.validators.llm_validator import validate_answers_with_llm
from liveweb_arena.utils.llm_client import LLMClient, LLMFatalError
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
        "coingecko": CoinGeckoPlugin,
        "tmdb": TMDBPlugin,
        "hybrid": HybridPlugin,
    }

    def __init__(self, api_key: str = None, use_cache: bool = True):
        """
        Initialize Actor.

        Args:
            api_key: API key for LLM service. Falls back to CHUTES_API_KEY env var.
            use_cache: Whether to use caching (default: True)

        Operating Modes:
            use_cache=True (Cache Mode):
                - API data: served from cache (refreshed every TTL seconds)
                - Web pages: served from HAR cache (recorded per seed + API version)
                - Benefit: Consistent data between agent view and ground truth
                - Benefit: Reduced website access (prevents IP blocking)
                - Benefit: Reproducible evaluations

            use_cache=False (Live Mode):
                - API data: fetched in real-time from live APIs
                - Web pages: fetched in real-time from live websites
                - Use case: Testing against current live data
                - Risk: Data may differ between agent view and ground truth fetch
        """
        self.api_key = api_key or os.getenv("CHUTES_API_KEY")
        self.browser: Optional[BrowserEngine] = None
        self.task_manager = TaskManager(self.PLUGINS)
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._lock = asyncio.Lock()
        self.use_cache = use_cache
        self._cache_initialized = False

    async def evaluate(
        self,
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        seed: Optional[int] = None,
        num_subtasks: int = 2,
        templates: Optional[List[tuple]] = None,
        max_steps: Optional[int] = None,
        timeout: int = 3600,
        temperature: float = 0.7,
        max_concurrency: int = 2,
        validation_model: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> dict:
        """
        Run a single evaluation.

        Args:
            model: Model name for the LLM agent
            base_url: OpenAI-compatible API base URL
            api_key: Override API key for this evaluation
            seed: Deterministic task generation seed (random if None)
            num_subtasks: Number of sub-tasks (1-4)
            templates: List of (plugin, template_name) tuples; None = random
            max_steps: Max browser interaction steps
            timeout: Total wall-clock budget in seconds
            temperature: LLM temperature
            max_concurrency: Container-local concurrency limit
            validation_model: Model for answer validation (default: same as model)
            task_id: Optional task ID for deterministic question type

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
                    templates=templates,
                    max_steps=max_steps,
                    timeout=timeout,
                    temperature=temperature,
                    validation_model=validation_model,
                    task_id=task_id,
                )
            except Exception as e:
                import traceback
                result = {
                    "task_name": f"liveweb_arena:{num_subtasks}tasks",
                    "score": 0.0,
                    "success": False,
                    "time_taken": time.time() - start_time,
                    "extra": {
                        "task_id": task_id,
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
        templates: Optional[List[tuple]],
        max_steps: Optional[int],
        timeout: int,
        temperature: float,
        validation_model: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> dict:
        """Internal evaluation logic"""
        await self._ensure_browser()

        # Initialize cache adapters (lazy, only once)
        if self.use_cache and not self._cache_initialized:
            get_adapter_registry()  # This initializes all adapters
            self._cache_initialized = True
            log("Actor", "Cache adapters initialized")

        task = await self.task_manager.generate_composite_task(
            seed=seed,
            num_subtasks=num_subtasks,
            templates=templates,
        )
        log("Actor", f"Generated {len(task.subtasks)} subtasks, seed={seed}")
        for i, subtask in enumerate(task.subtasks, 1):
            q = subtask.intent
            log("Actor", f"  [{i}] {q[:100]}{'...' if len(q) > 100 else ''}")

        # Calculate effective max_steps from subtasks
        total_expected_steps = sum(st.expected_steps for st in task.subtasks)
        if max_steps is None:
            # Auto mode: use task-based calculation
            effective_max_steps = total_expected_steps
        else:
            # Use the larger of provided max_steps and task requirements
            effective_max_steps = max(max_steps, total_expected_steps)
        log("Actor", f"Max steps: {effective_max_steps} (from {len(task.subtasks)} subtasks)")

        # Collect allowed domains from all plugins (whitelist)
        allowed_domains = set()
        for subtask in task.subtasks:
            plugin = self.task_manager.get_plugin(subtask.plugin_name)
            if plugin and hasattr(plugin, 'allowed_domains'):
                allowed_domains.update(plugin.allowed_domains)

        # Determine which cache sources are needed based on plugins
        cache_sources = []
        for subtask in task.subtasks:
            plugin_name = subtask.plugin_name
            if plugin_name == "hybrid":
                cache_sources.extend(["coingecko", "stooq"])
            elif plugin_name == "coingecko":
                cache_sources.append("coingecko")
            elif plugin_name == "stooq":
                cache_sources.append("stooq")
            elif plugin_name == "weather":
                cache_sources.append("weather")
            elif plugin_name == "tmdb":
                cache_sources.append("tmdb")
            # Note: taostats uses live API, no caching needed
        cache_sources = list(set(cache_sources))  # Dedupe

        # Initialize cache mode variables
        cache_context = None
        har_path = None
        har_mode = "off"

        # Set up caching based on mode
        if self.use_cache:
            # ===== CACHE MODE =====
            # Both API and web page (HAR) caching enabled
            # Ensures consistency: agent sees same data as ground truth
            log("Actor", "Mode: CACHE (API + HAR)")

            if cache_sources:
                try:
                    cache_manager = get_cache_manager()
                    cache_context = EvaluationCacheContext(
                        cache_manager=cache_manager,
                        sources=cache_sources,
                        ensure_fresh=True,
                    )
                    await cache_context.__aenter__()

                    # Set cache context for all API modules
                    set_cache_context(cache_context)
                    set_coingecko_cache_context(cache_context)
                    set_stooq_cache_context(cache_context)
                    set_weather_cache_context(cache_context)
                    set_tmdb_cache_context(cache_context)

                    cached = [s for s, v in cache_context.locked_versions.items() if v]
                    log("Actor", f"API cache: {cached}")

                    # Get HAR cache info (paired with API cache version, seed-independent)
                    har_path, har_mode = cache_context.get_har_cache_info()
                    log("Actor", f"HAR cache: {har_mode} -> {har_path.name if har_path else 'N/A'}")

                    # Acquire lock for recording (prevent concurrent writes)
                    if har_mode == "record":
                        if not cache_context.acquire_har_lock(har_path):
                            log("Actor", "HAR lock busy - waiting for other recording")
                            har_path = None
                            har_mode = "off"

                except Exception as e:
                    log("Actor", f"Cache setup failed: {e}, falling back to live mode")
                    cache_context = None
                    har_path = None
                    har_mode = "off"
        else:
            # ===== LIVE MODE =====
            # No caching - all data fetched in real-time
            log("Actor", "Mode: LIVE (real-time API + web)")

        # Create browser session with HAR caching
        session = await self.browser.new_session(har_path=har_path, har_mode=har_mode)

        # Set allowed domains
        if allowed_domains:
            await session.set_allowed_domains(list(allowed_domains))
            log("Actor", f"Allowed domains: {sorted(allowed_domains)}")

        # Collect and apply blocked URL patterns from all plugins in this task
        blocked_patterns = []
        for subtask in task.subtasks:
            plugin = self.task_manager.get_plugin(subtask.plugin_name)
            if plugin and hasattr(plugin, 'blocked_url_patterns'):
                blocked_patterns.extend(plugin.blocked_url_patterns)
        if blocked_patterns:
            await session.block_urls(list(set(blocked_patterns)))  # Dedupe
            log("Actor", f"Blocked URL patterns: {blocked_patterns}")

        try:
            llm_client = LLMClient(base_url=base_url, api_key=api_key)

            # Set up GroundTruthManager for triggered fetching
            gt_manager = GroundTruthManager(task_manager=self.task_manager)
            gt_manager.register_subtasks(task.subtasks)

            # Create navigation callback
            async def on_navigation(url: str):
                triggered = await gt_manager.check_triggers(url)
                for tag in triggered:
                    state = gt_manager.states.get(tag)
                    if state and state.fetches:
                        latest = state.fetches[-1]
                        if latest.error:
                            log("Actor", f"GT fetch error for {tag}: {latest.error}")
                        elif latest.value is not None:
                            val_str = str(latest.value)[:60]
                            log("Actor", f"GT fetch for {tag}: {val_str}...")
                        else:
                            log("Actor", f"GT fetch for {tag} returned None")

            agent_loop = AgentLoop(
                session=session,
                llm_client=llm_client,
                policy=AgentPolicy(),
                max_steps=effective_max_steps,
                on_navigation=on_navigation,
            )

            # Track failure reasons
            failure_reason = None
            fatal_error_message = None

            try:
                trajectory, final_answer, usage = await asyncio.wait_for(
                    agent_loop.run(task=task, model=model, temperature=temperature, seed=seed),
                    timeout=timeout,
                )
                # Check if max steps reached without completion
                if agent_loop.is_max_steps_reached():
                    failure_reason = "max_steps_reached"
                    log("Actor", "Max steps reached without completion - marking as failed", force=True)
            except asyncio.TimeoutError:
                failure_reason = "agent_timeout"
                log("Actor", f"Agent timeout after {timeout}s", force=True)
                trajectory = agent_loop.get_trajectory()
                final_answer = agent_loop.get_final_answer()
                usage = agent_loop.get_usage()
            except LLMFatalError as e:
                failure_reason = "llm_error"
                fatal_error_message = str(e)
                log("Actor", f"LLM fatal error: {e}", force=True)
                trajectory = agent_loop.get_trajectory()
                final_answer = agent_loop.get_final_answer()
                usage = agent_loop.get_usage()
            except BrowserFatalError as e:
                failure_reason = "browser_error"
                fatal_error_message = str(e)
                log("Actor", f"Browser fatal error: {e}", force=True)
                trajectory = agent_loop.get_trajectory()
                final_answer = agent_loop.get_final_answer()
                usage = agent_loop.get_usage()

            # Fetch remaining ground truths (including legacy subtasks without triggers)
            await gt_manager.fetch_remaining(subtasks=task.subtasks)
            ground_truths = gt_manager.get_ground_truths()

            # Log fetch summary
            for line in gt_manager.get_fetch_summary():
                log("Actor", line, force="error" in line.lower())

            # Log ground truth stats
            gt_stats = gt_manager.get_stats()
            log("Actor", f"Ground truths: {gt_stats['triggered']} triggered, {gt_stats['fallback']} fallback")
            for tag, gt in ground_truths.items():
                gt_str = str(gt)[:100] + "..." if len(str(gt)) > 100 else str(gt)
                log("Actor", f"  [{tag}] Expected: {gt_str}")

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
            result = {
                "task_name": f"liveweb_arena:{num_subtasks}tasks",
                "score": total_score,
                "success": success,
                "time_taken": 0.0,  # Will be set by caller
                "extra": {
                    "task_id": task_id,
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

            # Add top-level error field if there was a fatal error
            if fatal_error_message:
                result["error"] = fatal_error_message

            return result

        finally:
            # Log HAR info before closing session
            har_info = session.get_har_info()
            if har_info:
                log("Actor", f"HAR cache: mode={har_info['mode']}, saved={har_info['path']}")

            # Always close the session (this saves HAR file if recording)
            await session.close()

            # Post-recording validation and lock release
            if har_mode == "record" and har_path and cache_context:
                # Release the recording lock
                cache_context.release_har_lock(har_path)

                # Validate the recorded HAR file
                if har_path.exists():
                    if cache_context._validate_har_file(har_path):
                        size_kb = har_path.stat().st_size / 1024
                        log("Actor", f"HAR recording validated: {size_kb:.1f} KB")
                    else:
                        log("Actor", "HAR recording invalid or incomplete - will re-record on next run")
                        try:
                            har_path.unlink()
                        except OSError:
                            pass
                else:
                    log("Actor", "HAR recording failed - file not created")

            # Clean up cache context
            if cache_context is not None:
                await cache_context.__aexit__(None, None, None)
                set_cache_context(None)
                set_coingecko_cache_context(None)
                set_stooq_cache_context(None)
                set_weather_cache_context(None)
                set_tmdb_cache_context(None)

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
        from liveweb_arena.core.agent_policy import AgentPolicy

        conversation = []

        # Build full system prompt (same as what's sent to LLM)
        policy = AgentPolicy()
        system_content = policy.build_system_prompt(task)

        conversation.append({
            "role": "system",
            "content": system_content,
            "metadata": {
                "type": "instructions",
                "plugins": list(task.plugin_hints.keys()) if task.plugin_hints else [],
                "num_subtasks": len(task.subtasks),
            }
        })

        # Note: Task questions are already included in system_content via build_system_prompt
        # No need for a separate user message with tasks

        # Alternating user (environment) and assistant (agent) turns
        for step in trajectory:
            # User turn: environment observation
            obs_content = (
                f"URL: {step.observation.url}\n"
                f"Title: {step.observation.title}\n"
                f"Page Content:\n{step.observation.accessibility_tree}"
            )

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
