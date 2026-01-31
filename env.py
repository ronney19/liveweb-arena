"""LiveWeb Arena - Main evaluation entry point"""

import asyncio
import os
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Type

from liveweb_arena.core.browser import BrowserEngine
from liveweb_arena.core.task_manager import TaskManager
from liveweb_arena.core.agent_policy import AgentPolicy
from liveweb_arena.core.agent_loop import AgentLoop, BrowserFatalError
from liveweb_arena.core.parser import AnswerParser
from liveweb_arena.core.gt_collector import GTCollector, GTSourceType, set_current_gt_collector
from liveweb_arena.core.cache import CacheManager, CachedPage, CacheFatalError, PageRequirement, normalize_url
from liveweb_arena.core.interceptor import CacheInterceptor, clear_cached_accessibility_trees
from liveweb_arena.plugins.base import BasePlugin
from liveweb_arena.plugins import get_plugin, get_all_plugins
from liveweb_arena.core.validators.llm_validator import validate_answers_with_llm
from liveweb_arena.utils.llm_client import LLMClient, LLMFatalError
from liveweb_arena.utils.logger import log


class Actor:
    """
    LiveWeb Arena evaluation actor.

    Evaluates LLM browser agents on real-world web interaction tasks.
    Features:
    - On-demand page caching with 24-hour TTL
    - Ground truth extraction from pages agent visits
    - Plugin-based architecture for extensible task types
    - LLM-based flexible answer validation
    """

    def __init__(
        self,
        api_key: str = None,
        cache_dir: Optional[Path] = None,
        use_cache: bool = True,
    ):
        """
        Initialize Actor.

        Args:
            api_key: API key for LLM service. Falls back to API_KEY env var.
            cache_dir: Cache directory (default: ./cache)
            use_cache: Whether to use cache (True) or live mode (False)
        """
        self.api_key = api_key or os.getenv("API_KEY")
        self.browser: Optional[BrowserEngine] = None
        self.task_manager = TaskManager(get_all_plugins())
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._lock = asyncio.Lock()
        self.use_cache = use_cache

        # Initialize cache manager
        if cache_dir is None:
            # Check environment variable first
            env_cache_dir = os.environ.get("LIVEWEB_CACHE_DIR")
            if env_cache_dir:
                cache_dir = Path(env_cache_dir)
            else:
                cache_dir = Path("/var/lib/liveweb-arena/cache")
        self.cache_manager = CacheManager(cache_dir)

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
        """Internal evaluation logic."""
        await self._ensure_browser()

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
            effective_max_steps = total_expected_steps
        else:
            effective_max_steps = max(max_steps, total_expected_steps)
        log("Actor", f"Max steps: {effective_max_steps} (from {len(task.subtasks)} subtasks)")

        # Collect allowed domains and blocked patterns from all plugins
        allowed_domains: Set[str] = set()
        blocked_patterns: List[str] = []
        plugins_used: Dict[str, BasePlugin] = {}

        for subtask in task.subtasks:
            plugin = self.task_manager.get_plugin(subtask.plugin_name)
            if plugin:
                plugins_used[subtask.plugin_name] = plugin
                if hasattr(plugin, 'allowed_domains'):
                    allowed_domains.update(plugin.allowed_domains)
                if hasattr(plugin, 'get_blocked_patterns'):
                    blocked_patterns.extend(plugin.get_blocked_patterns())
                elif hasattr(plugin, 'blocked_url_patterns'):
                    blocked_patterns.extend(plugin.blocked_url_patterns)

        blocked_patterns = list(set(blocked_patterns))
        if blocked_patterns:
            log("Actor", f"Blocked URL patterns: {blocked_patterns}")

        # Prepare cached pages (on-demand caching)
        cached_pages: Dict[str, CachedPage] = {}

        if self.use_cache:
            log("Actor", "Mode: CACHE (on-demand caching)")
        else:
            log("Actor", "Mode: LIVE (no caching)")

        # Create browser session
        session = await self.browser.new_session()

        # Clear cached accessibility trees from previous runs
        clear_cached_accessibility_trees()

        # Set up interceptor
        interceptor = CacheInterceptor(
            cached_pages=cached_pages,
            allowed_domains=allowed_domains,
            blocked_patterns=blocked_patterns if blocked_patterns else None,
            cache_manager=self.cache_manager if self.use_cache else None,
        )

        # Install route handler using session's set_cache_interceptor method
        if self.use_cache:
            await session.set_cache_interceptor(interceptor)

        # Block URLs in live mode
        if not self.use_cache and blocked_patterns:
            await session.block_urls(blocked_patterns)

        try:
            llm_client = LLMClient(base_url=base_url, api_key=api_key)

            # Initialize unified GT collector
            gt_collector = GTCollector(
                subtasks=task.subtasks,
                task_manager=self.task_manager,
            )
            # Set global reference for hybrid utils
            set_current_gt_collector(gt_collector)

            # Track accessibility trees for real-time GT collection
            step_accessibility_trees: Dict[str, str] = {}

            # Create navigation callback for caching and GT tracking
            async def on_navigation(url: str):
                # Cache the page on navigation if in cache mode
                if self.use_cache:
                    normalized = normalize_url(url)
                    if normalized not in cached_pages:
                        # Determine which plugin to use for API data
                        plugin = None
                        for p in plugins_used.values():
                            for domain in p.allowed_domains:
                                if domain in url.lower():
                                    plugin = p
                                    break
                            if plugin:
                                break

                        if plugin:
                            # Check if this page needs API data (detail page vs navigation page)
                            need_api = plugin.needs_api_data(url)
                            page_req = PageRequirement.data(url) if need_api else PageRequirement.nav(url)

                            # Fetch and cache the page - raise fatal error on failure
                            # Cache failure = browser can't load page = invalid evaluation
                            pages = await self.cache_manager.ensure_cached(
                                [page_req],
                                plugin,
                            )
                            cached_pages.update(pages)
                            req_type = "data" if need_api else "nav"
                            log("Actor", f"Cached ({req_type}): {url[:55]}...")

            # Observation callback for real-time GT collection (fires when page is viewed)
            async def on_observation(obs):
                """Called when agent observes a page (before deciding action)."""
                if obs and obs.url:
                    url = obs.url
                    if url and url != "about:blank":
                        # Get api_data from cached page (CACHE mode) or fetch live (LIVE mode)
                        api_data = None
                        if self.use_cache:
                            # CACHE mode: use cached api_data
                            normalized = normalize_url(url)
                            cached_page = cached_pages.get(normalized)
                            if cached_page:
                                api_data = cached_page.api_data
                        else:
                            # LIVE mode: fetch api_data from network
                            # This ensures GT matches what agent sees in real-time
                            for p in plugins_used.values():
                                for domain in p.allowed_domains:
                                    if domain in url.lower():
                                        try:
                                            api_data = await p.fetch_api_data(url)
                                        except Exception:
                                            pass
                                        break
                                if api_data:
                                    break

                        # Collect GT from this page visit
                        await gt_collector.on_page_visit(
                            url,
                            obs.accessibility_tree,
                            api_data=api_data,
                        )

            agent_loop = AgentLoop(
                session=session,
                llm_client=llm_client,
                policy=AgentPolicy(),
                max_steps=effective_max_steps,
                on_navigation=on_navigation,
                on_observation=on_observation,
            )

            # Failure tracking:
            #   failure_reason: what happened (always set on failure, goes into extra)
            #   error_message: set = evaluation is INVALID (mechanism issue, not agent capability)
            #     Valid failures (no error_message): max_steps_reached
            #     Invalid failures (error_message set): llm_error, browser_error, cache_error, agent_timeout, gt_failure
            failure_reason = None
            error_message = None

            _FATAL_ERROR_MAP = {
                LLMFatalError: "llm_error",
                BrowserFatalError: "browser_error",
                CacheFatalError: "cache_error",
            }

            try:
                trajectory, final_answer, usage = await asyncio.wait_for(
                    agent_loop.run(task=task, model=model, temperature=temperature, seed=seed),
                    timeout=timeout,
                )
                if agent_loop.is_max_steps_reached():
                    failure_reason = "max_steps_reached"
                    log("Actor", "Max steps reached without completion - marking as failed", force=True)
            except asyncio.TimeoutError:
                failure_reason = "agent_timeout"
                error_message = f"Agent timeout after {timeout}s"
                log("Actor", error_message, force=True)
            except (LLMFatalError, BrowserFatalError, CacheFatalError) as e:
                failure_reason = _FATAL_ERROR_MAP[type(e)]
                error_message = f"{failure_reason}: {e}"
                log("Actor", f"Fatal error - {error_message}", force=True)

            # Exception path: recover partial state from agent loop
            if failure_reason and failure_reason != "max_steps_reached":
                trajectory = agent_loop.get_trajectory()
                final_answer = agent_loop.get_final_answer()
                usage = agent_loop.get_usage()

            # GT is collected in real-time via on_observation callback
            # For API_ONLY and HYBRID templates, fetch remaining API GT
            # HYBRID templates use collected api_data from page visits
            await gt_collector.fetch_remaining_api_gt()

            # Clean up GT collector reference
            set_current_gt_collector(None)

            # Build ground truths based on template's declared source type
            ground_truths = {}
            gt_extraction_failures = {}

            for subtask in task.subtasks:
                tag = subtask.answer_tag
                gt_value = gt_collector.get_gt_for_subtask(subtask)

                if gt_value is not None:
                    ground_truths[tag] = gt_value
                else:
                    reason = gt_collector.get_failure_reason(subtask)
                    gt_extraction_failures[tag] = reason
                    log("Actor", f"GT [{tag}] FAILED: {reason}", force=True)

            # Single summary line
            stats = gt_collector.get_stats()
            log("Actor", f"GT: {len(ground_truths)} ok, {len(gt_extraction_failures)} failed, {stats['collected_assets']} assets collected")

            # Parse answers
            parser = AnswerParser()
            parsed_answers = parser.parse_answers(final_answer, num_subtasks)
            output_format = parser.get_output_format(final_answer)
            validation_rules = {}
            for subtask in task.subtasks:
                plugin = self.task_manager.get_plugin(subtask.plugin_name)
                if hasattr(plugin, 'get_validation_rules'):
                    validation_rules[subtask.answer_tag] = plugin.get_validation_rules(
                        subtask.validation_info
                    )

            # Handle GT extraction failures - these get 0 score immediately
            # Only validate subtasks that have GT available
            subtasks_to_validate = []
            pre_failed_validations = []

            for subtask in task.subtasks:
                tag = subtask.answer_tag
                if tag in gt_extraction_failures:
                    # GT extraction failed - agent couldn't have gotten correct data either
                    pre_failed_validations.append({
                        "question": subtask.intent,
                        "answer_tag": tag,
                        "expected": None,
                        "actual": parsed_answers.get(tag),
                        "score": 0.0,
                        "is_correct": False,
                        "reasoning": f"GT unavailable: {gt_extraction_failures[tag]}",
                    })
                else:
                    subtasks_to_validate.append(subtask)

            # Use LLM to validate answers with available GT
            answer_validations = pre_failed_validations.copy()

            if subtasks_to_validate:
                actual_validation_model = validation_model or "openai/gpt-oss-120b-TEE"
                llm_validations = await validate_answers_with_llm(
                    llm_client=llm_client,
                    subtasks=subtasks_to_validate,
                    answers=parsed_answers,
                    ground_truths=ground_truths,
                    validation_rules=validation_rules,
                    model=model,
                    validation_model=actual_validation_model,
                )
                answer_validations.extend(llm_validations)

            # Calculate overall score
            if failure_reason:
                total_score = 0.0
                success = False
            elif answer_validations:
                total_score = sum(v["score"] for v in answer_validations) / len(answer_validations)
                success = total_score >= 0.8
            else:
                total_score = 0.0
                success = False

            # Get interceptor stats
            interceptor_stats = interceptor.get_stats()
            log("Actor", f"Cache stats: {interceptor_stats['hits']} hits, {interceptor_stats['misses']} misses, "
                f"{interceptor_stats['blocked']} blocked")

            # Get final URL
            final_url = None
            if trajectory:
                final_url = trajectory[-1].observation.url

            # Build conversation history
            conversation = self._build_conversation(task, trajectory)

            result = {
                "task_name": f"liveweb_arena:{num_subtasks}tasks",
                "score": total_score,
                "success": success,
                "time_taken": 0.0,
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
                    "cache_stats": interceptor_stats,
                },
            }

            # GT failure is also a mechanism issue — set error if not already set
            if not error_message and gt_extraction_failures:
                failure_details = "; ".join(
                    f"[{tag}] {reason}" for tag, reason in gt_extraction_failures.items()
                )
                error_message = f"GT extraction failed: {failure_details}"

            if error_message:
                result["error"] = error_message

            return result

        finally:
            await session.close()

    async def _ensure_browser(self):
        """Ensure browser is started (lazy initialization)."""
        async with self._lock:
            if self.browser is None:
                self.browser = BrowserEngine(headless=True)
                await self.browser.start()

    async def shutdown(self):
        """Shutdown browser and cleanup resources."""
        if self.browser:
            await self.browser.stop()
            self.browser = None

    def _build_conversation(
        self,
        task,
        trajectory: List,
    ) -> List[dict]:
        """Build conversation history from task and trajectory."""
        from liveweb_arena.core.agent_policy import AgentPolicy

        conversation = []

        # Build system prompt
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

        # Alternating user (environment) and assistant (agent) turns
        for step in trajectory:
            conversation.append({
                "role": "user",
                "content": step.prompt,
                "metadata": {
                    "type": "environment",
                    "step": step.step_num,
                    "url": step.observation.url,
                }
            })

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
