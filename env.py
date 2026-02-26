"""LiveWeb Arena - Main evaluation entry point"""

import asyncio
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from liveweb_arena.core.browser import BrowserEngine, BrowserSession
from liveweb_arena.core.task_manager import TaskManager
from liveweb_arena.core.agent_policy import AgentPolicy
from liveweb_arena.core.agent_loop import AgentLoop, BrowserFatalError
from liveweb_arena.core.parser import AnswerParser
from liveweb_arena.core.gt_collector import GTCollector, GTSourceType, set_current_gt_collector
from liveweb_arena.core.cache import CacheManager, CachedPage, CacheFatalError, PageRequirement, normalize_url
from liveweb_arena.core.interceptor import CacheInterceptor
from liveweb_arena.core.models import BrowserObservation, CompositeTask, TrajectoryStep
from liveweb_arena.core.reward import StepwiseRewardCalculator, RewardConfig, RewardBreakdown
from liveweb_arena.plugins.base import BasePlugin
from liveweb_arena.plugins import get_plugin, get_all_plugins
from liveweb_arena.core.validators.llm_validator import validate_answers_with_llm
from liveweb_arena.utils.llm_client import LLMClient, LLMFatalError
from liveweb_arena.utils.logger import log
from urllib.parse import urlparse

# Import OpenEnvResponse from affinetes
from affinetes.core.openenv import OpenEnvResponse


def _url_matches_domain(url: str, allowed_domain: str) -> bool:
    """
    Check if URL belongs to an allowed domain.

    Uses proper domain matching (exact or subdomain), NOT substring matching.
    This prevents attacks like URL path injection.

    Args:
        url: Full URL to check
        allowed_domain: Domain name to match against (e.g., "coingecko.com")

    Returns:
        True if URL's domain matches or is subdomain of allowed_domain
    """
    try:
        parsed = urlparse(url.lower())
        domain = parsed.netloc
        # Remove port if present
        if ":" in domain:
            domain = domain.split(":")[0]
        # Exact match or subdomain match
        return domain == allowed_domain or domain.endswith("." + allowed_domain)
    except Exception:
        return False


@dataclass
class EpisodeState:
    """Internal state for a training episode."""
    # Core identifiers
    episode_id: str
    task_id: Optional[int]
    seed: int

    # Task context
    task: CompositeTask
    plugins_used: Dict[str, BasePlugin]
    allowed_domains: Set[str]
    blocked_patterns: List[str]

    # Browser state
    session: BrowserSession
    interceptor: CacheInterceptor
    cached_pages: Dict[str, CachedPage]

    # GT collection state
    gt_collector: GTCollector

    # Agent state
    policy: AgentPolicy
    system_prompt: str

    # Step tracking
    current_step: int = 0
    max_steps: int = 30
    trajectory: List[TrajectoryStep] = field(default_factory=list)

    # Completion state
    done: bool = False
    truncated: bool = False
    final_answer: Optional[Dict] = None
    failure_reason: Optional[str] = None

    # Metrics
    start_time: float = field(default_factory=time.time)
    last_observation: Optional[BrowserObservation] = None

    # Step-wise reward tracking
    reward_calculator: Optional[StepwiseRewardCalculator] = None
    cumulative_reward: float = 0.0
    reward_history: List[RewardBreakdown] = field(default_factory=list)


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
        self.api_key = api_key or os.getenv("API_KEY") or os.getenv("CHUTES_API_KEY")
        self.browser: Optional[BrowserEngine] = None
        self.task_manager = TaskManager(get_all_plugins())
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._lock = asyncio.Lock()
        self.use_cache = use_cache

        # Episode storage for OpenEnv interface
        self._episodes: Dict[str, EpisodeState] = {}

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
        num_subtasks: Optional[int] = None,
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
            templates: List of (plugin, template_name) tuples; None = from task_id or random
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

        # Parse task_id to get templates and other config if not explicitly provided
        if task_id is not None and templates is None:
            from liveweb_arena.core.task_registry import parse_task_id
            task_config = parse_task_id(task_id)
            templates = task_config["templates"]
            # Use task_id's num_tasks if not explicitly provided
            if num_subtasks is None:
                num_subtasks = task_config["num_tasks"]
            # Use variation_seed if no seed provided
            if seed is None:
                seed = task_config["variation_seed"]
            log("Actor", f"Task ID {task_id} -> templates={templates}, num_subtasks={num_subtasks}")

        # Apply defaults if still None
        if num_subtasks is None:
            num_subtasks = 2
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
                    "error": traceback.format_exc(),
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
                # Clear dynamic state for plugins supporting external navigation
                if hasattr(plugin, 'clear_external_urls'):
                    plugin.clear_external_urls()

        blocked_patterns = list(set(blocked_patterns))
        if blocked_patterns:
            log("Actor", f"Blocked URL patterns: {blocked_patterns}")

        # Prepare cached pages (on-demand caching)
        cached_pages: Dict[str, CachedPage] = {}

        if self.use_cache:
            log("Actor", "Mode: CACHE (on-demand caching)")
        else:
            log("Actor", "Mode: LIVE (no caching)")

        # Initialize variables for finally block cleanup
        session = None
        interceptor = None
        gt_collector = None

        # Create URL validator for plugins supporting external navigation
        def url_validator(url: str) -> bool:
            """Check if any plugin allows this URL (for dynamic external navigation)."""
            for p in plugins_used.values():
                if hasattr(p, 'is_url_allowed') and p.is_url_allowed(url):
                    return True
            return False

        try:
            # Create browser session
            session = await self.browser.new_session()

            # Set up interceptor
            interceptor = CacheInterceptor(
                cached_pages=cached_pages,
                allowed_domains=allowed_domains,
                blocked_patterns=blocked_patterns if blocked_patterns else None,
                cache_manager=self.cache_manager if self.use_cache else None,
                url_validator=url_validator,
            )

            # Install route handler using session's set_cache_interceptor method
            if self.use_cache:
                await session.set_cache_interceptor(interceptor)

            # Block URLs in live mode
            if not self.use_cache and blocked_patterns:
                await session.block_urls(blocked_patterns)

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

            # Helper to find plugin for a URL (includes external URL support)
            def find_plugin_for_url(url: str):
                """Find the plugin that handles this URL."""
                # First try domain matching
                for p in plugins_used.values():
                    for domain in p.allowed_domains:
                        if _url_matches_domain(url, domain):
                            return p
                # Then check dynamic URL validation (for external navigation)
                for p in plugins_used.values():
                    if hasattr(p, 'is_url_allowed') and p.is_url_allowed(url):
                        return p
                return None

            # Create navigation callback for caching and GT tracking
            async def on_navigation(url: str):
                # Cache the page on navigation if in cache mode
                if self.use_cache:
                    normalized = normalize_url(url)
                    if normalized not in cached_pages:
                        # Determine which plugin to use for API data
                        plugin = find_plugin_for_url(url)

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

                            # For plugins with external URL support, register external URLs
                            # from cached api_data (needed when cache HIT bypasses fetch_api_data)
                            if hasattr(plugin, '_extract_external_urls'):
                                for cached_page in pages.values():
                                    if cached_page.api_data:
                                        plugin._extract_external_urls(cached_page.api_data)

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
                            plugin = find_plugin_for_url(url)
                            if plugin:
                                need_api = plugin.needs_api_data(url)
                                if need_api:
                                    # Data page: API fetch must succeed
                                    try:
                                        api_data = await plugin.fetch_api_data(url)
                                    except Exception as e:
                                        raise CacheFatalError(
                                            f"LIVE mode API fetch failed (GT invalid): {e}",
                                            url=url,
                                        )
                                    if not api_data:
                                        raise CacheFatalError(
                                            f"LIVE mode API returned empty data (GT invalid)",
                                            url=url,
                                        )

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

            # Fatal errors that invalidate evaluation (system issues, not agent capability)
            _FATAL_ERROR_MAP = {
                LLMFatalError: "llm_error",
                CacheFatalError: "cache_error",
            }

            try:
                trajectory, final_answer, usage = await asyncio.wait_for(
                    agent_loop.run(task=task, model=model, temperature=temperature, seed=seed),
                    timeout=timeout,
                )
                if agent_loop.is_parse_failed():
                    failure_reason = "parse_failed"
                    log("Actor", "Parse failed - model output not valid JSON", force=True)
                elif agent_loop.is_max_steps_reached():
                    failure_reason = "max_steps_reached"
                    log("Actor", "Max steps reached without completion - marking as failed", force=True)
            except asyncio.TimeoutError:
                failure_reason = "agent_timeout"
                error_message = f"Agent timeout after {timeout}s"
                log("Actor", error_message, force=True)
            except BrowserFatalError as e:
                # Browser navigation failure = agent capability issue (e.g., invalid URL)
                # Valid evaluation with score=0, not a system error
                failure_reason = "browser_error"
                log("Actor", f"Browser error (agent issue): {e}", force=True)
            except (LLMFatalError, CacheFatalError) as e:
                failure_reason = _FATAL_ERROR_MAP[type(e)]
                error_message = f"{failure_reason}: {e}"
                log("Actor", f"Fatal error - {error_message}", force=True)

            # Exception path: recover partial state from agent loop
            # (parse_failed and max_steps_reached are normal exits, not exceptions)
            if failure_reason and failure_reason not in ("max_steps_reached", "parse_failed"):
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
                        "reasoning": f"Data not collected: {gt_extraction_failures[tag]}",
                    })
                else:
                    subtasks_to_validate.append(subtask)

            # Use LLM to validate answers with available GT
            answer_validations = pre_failed_validations.copy()

            if subtasks_to_validate:
                actual_validation_model = validation_model or "zai-org/GLM-4.7"
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
            # Hard failures (system issues) always get 0 — evaluation is invalid
            # Soft failures (max_steps, parse_failed) use computed scores if available
            # browser_error = agent capability issue (not system error), uses computed scores
            _HARD_FAILURES = {"agent_timeout", "llm_error", "cache_error"}
            if failure_reason and failure_reason in _HARD_FAILURES:
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

            # GT failure handling: distinguish between valid and invalid evaluations
            # - DATA_NOT_COLLECTED: Agent didn't visit required pages (valid eval, no error)
            # - SYSTEM_ERROR: Network/parsing/template bugs (invalid eval, set error)
            if not error_message and gt_extraction_failures:
                # Check if any GT failure is a system error (invalid evaluation)
                system_errors = []
                for subtask in task.subtasks:
                    tag = subtask.answer_tag
                    if tag in gt_extraction_failures and gt_collector.is_system_error(subtask):
                        system_errors.append(f"[{tag}] {gt_extraction_failures[tag]}")

                if system_errors:
                    error_message = f"GT system error: {'; '.join(system_errors)}"

            if error_message:
                result["error"] = error_message

            return result

        finally:
            # Clean up to prevent memory leaks
            set_current_gt_collector(None)
            if gt_collector is not None:
                gt_collector.cleanup()
            if interceptor is not None:
                interceptor.cleanup()
            cached_pages.clear()
            if session is not None:
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

            conversation.append({
                "role": "assistant",
                "content": step.raw_response,
                "metadata": {
                    "type": "agent_action",
                    "step": step.step_num,
                    "action_type": step.action.action_type if step.action else None,
                    "action_result": step.action_result,
                }
            })

        return conversation

    # ========== OpenEnv Interface ==========

    async def reset(
        self,
        task_id: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> OpenEnvResponse:
        """
        Reset environment and start a new episode.

        Args:
            task_id: Task identifier for deterministic question generation
            seed: Random seed for variation

        Returns:
            OpenEnvResponse with initial observation
        """
        # Generate seed if not provided
        seed = seed if seed is not None else random.randint(0, 2**32 - 1)

        # Parse task_id to get templates and config
        templates = None
        num_subtasks = 2
        if task_id is not None:
            from liveweb_arena.core.task_registry import parse_task_id
            task_config = parse_task_id(task_id)
            templates = task_config["templates"]
            num_subtasks = task_config["num_tasks"]
            # Use variation_seed from task_id if seed was auto-generated
            if seed == task_config.get("variation_seed"):
                pass  # Keep the provided seed
            log("Actor", f"Reset: task_id={task_id} -> templates={templates}, num_subtasks={num_subtasks}")
        else:
            # Generate a task_id from seed for reproducibility
            task_id = (seed & 0x7FFFFFFF)
            log("Actor", f"Reset: generated task_id={task_id} from seed={seed}")

        # Ensure browser is started
        await self._ensure_browser()

        # Generate task
        task = await self.task_manager.generate_composite_task(
            seed=seed,
            num_subtasks=num_subtasks,
            templates=templates,
        )
        log("Actor", f"Generated {len(task.subtasks)} subtasks, seed={seed}")

        # Calculate max_steps from subtasks
        total_expected_steps = sum(st.expected_steps for st in task.subtasks)
        max_steps = max(30, total_expected_steps)

        # Collect allowed domains and blocked patterns from plugins
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
                # Clear dynamic state for plugins supporting external navigation
                if hasattr(plugin, 'clear_external_urls'):
                    plugin.clear_external_urls()

        blocked_patterns = list(set(blocked_patterns))

        # Prepare cached pages storage
        cached_pages: Dict[str, CachedPage] = {}

        # Initialize variables for cleanup on failure
        session = None
        interceptor = None
        gt_collector = None
        episode_added = False

        # Create URL validator for plugins supporting external navigation
        def url_validator(url: str) -> bool:
            """Check if any plugin allows this URL (for dynamic external navigation)."""
            for p in plugins_used.values():
                if hasattr(p, 'is_url_allowed') and p.is_url_allowed(url):
                    return True
            return False

        try:
            # Create browser session
            session = await self.browser.new_session()

            # Set up interceptor
            interceptor = CacheInterceptor(
                cached_pages=cached_pages,
                allowed_domains=allowed_domains,
                blocked_patterns=blocked_patterns if blocked_patterns else None,
                cache_manager=self.cache_manager if self.use_cache else None,
                url_validator=url_validator,
            )

            # Install route handler
            if self.use_cache:
                await session.set_cache_interceptor(interceptor)

            # Block URLs in live mode
            if not self.use_cache and blocked_patterns:
                await session.block_urls(blocked_patterns)

            # Initialize GT collector
            gt_collector = GTCollector(
                subtasks=task.subtasks,
                task_manager=self.task_manager,
            )
            set_current_gt_collector(gt_collector)

            # Initialize step-wise reward calculator
            target_assets: Set[str] = set()
            required_domains: Set[str] = set()
            reward_overrides: Dict[str, float] = {}

            for subtask in task.subtasks:
                template = subtask.template
                if template:
                    # Collect target assets from all subtasks
                    target_assets.update(
                        template.get_target_assets(subtask.validation_info)
                    )
                    # Collect required domains from all subtasks
                    required_domains.update(
                        template.get_required_domains(subtask.validation_info)
                    )
                    # Merge reward overrides (later subtasks can override earlier)
                    overrides = template.get_reward_overrides()
                    if overrides:
                        reward_overrides.update(overrides)

            reward_config = RewardConfig(**reward_overrides) if reward_overrides else RewardConfig()
            reward_calculator = StepwiseRewardCalculator(
                config=reward_config,
                target_assets=target_assets,
                required_domains=required_domains,
            )

            # Build agent policy and system prompt
            policy = AgentPolicy()
            system_prompt = policy.build_system_prompt(task)

            # Navigate to about:blank and get initial observation
            obs = await session.goto("about:blank")

            # Create episode state
            episode_id = uuid.uuid4().hex
            episode = EpisodeState(
                episode_id=episode_id,
                task_id=task_id,
                seed=seed,
                task=task,
                plugins_used=plugins_used,
                allowed_domains=allowed_domains,
                blocked_patterns=blocked_patterns,
                session=session,
                interceptor=interceptor,
                cached_pages=cached_pages,
                gt_collector=gt_collector,
                policy=policy,
                system_prompt=system_prompt,
                max_steps=max_steps,
                last_observation=obs,
                reward_calculator=reward_calculator,
            )
            self._episodes[episode_id] = episode
            episode_added = True

            # Build observation string (system prompt + initial page state)
            observation = self._format_observation(episode, obs, is_initial=True)

            return OpenEnvResponse(
                observation=observation,
                episode_id=episode_id,
                info=self._build_info(episode),
            )

        except Exception:
            # Clean up resources if episode was not successfully added
            if not episode_added:
                set_current_gt_collector(None)
                if gt_collector is not None:
                    gt_collector.cleanup()
                if interceptor is not None:
                    interceptor.cleanup()
                cached_pages.clear()
                if session is not None:
                    await session.close()
            raise

    async def step(
        self,
        action: str,
        episode_id: Optional[str] = None,
    ) -> OpenEnvResponse:
        """
        Execute an action in the environment.

        Args:
            action: The action string (full LLM response including <think> tags)
            episode_id: Episode identifier

        Returns:
            OpenEnvResponse with new observation
        """
        # Validate episode
        if not episode_id:
            return OpenEnvResponse(
                observation="No episode_id provided. Call reset() first.",
                done=True,
                truncated=True,
                info={"error": {"type": "no_episode_id", "retryable": True}},
            )

        episode = self._episodes.get(episode_id)
        if not episode:
            return OpenEnvResponse(
                observation=f"Episode {episode_id} not found. Call reset() first.",
                done=True,
                truncated=True,
                info={"error": {"type": "episode_not_found", "retryable": True}},
            )

        if episode.done:
            return OpenEnvResponse(
                observation="Episode already finished. Call reset() to start a new one.",
                episode_id=episode_id,
                done=True,
                info=self._build_info(episode, {"type": "episode_done", "retryable": True}),
            )

        # Parse the action using agent policy
        parsed_action = episode.policy.parse_response(action)
        if parsed_action is None:
            # Parse failed - record in trajectory but don't terminate
            episode.current_step += 1
            step = TrajectoryStep(
                step_num=episode.current_step - 1,
                observation=episode.last_observation,
                action=None,
                action_result="Parse failed - model output not valid JSON",
                prompt=self._format_observation(episode, episode.last_observation),
                raw_response=action,
            )
            episode.trajectory.append(step)

            # Calculate parse failure penalty
            step_reward = 0.0
            reward_breakdown = RewardBreakdown()
            if episode.reward_calculator:
                reward_breakdown = episode.reward_calculator.calculate_step_reward(
                    url=episode.last_observation.url if episode.last_observation else "",
                    action_result="Parse failed",
                    collected_asset_ids=set(episode.gt_collector.get_collected_api_data().keys()),
                    parse_failed=True,
                )
                step_reward = reward_breakdown.total
                episode.cumulative_reward += step_reward
                episode.reward_history.append(reward_breakdown)

            return OpenEnvResponse(
                observation=f"Action parse failed. Please provide a valid JSON action.\n\n{self._format_observation(episode, episode.last_observation)}",
                episode_id=episode_id,
                reward=step_reward,
                info=self._build_info(episode, {"type": "action_parse", "retryable": True}, reward_breakdown),
            )

        # Handle stop action
        if parsed_action.action_type == "stop":
            episode.done = True
            final_params = parsed_action.params.get("final", {})
            episode.final_answer = final_params if final_params else parsed_action.params

            step = TrajectoryStep(
                step_num=episode.current_step,
                observation=episode.last_observation,
                action=parsed_action,
                action_result="Task completed",
                prompt=self._format_observation(episode, episode.last_observation),
                raw_response=action,
            )
            episode.trajectory.append(step)
            log("Actor", f"Episode {episode_id[:8]}... completed with stop action")

            return OpenEnvResponse(
                observation="Task completed. Episode finished.",
                episode_id=episode_id,
                reward=0.0,  # Reward computed in validation phase
                done=True,
                info=self._build_info(episode),
            )

        # Execute browser action
        episode.current_step += 1
        old_url = episode.last_observation.url if episode.last_observation else None

        try:
            obs = await episode.session.execute_action(parsed_action)
            action_result = "Success"
        except Exception as e:
            action_result = f"Failed: {e}"
            obs = await episode.session.get_observation()

        # Fire navigation callback if URL changed (for caching)
        if obs.url != old_url and obs.url != "about:blank":
            await self._on_episode_navigation(episode, obs.url)

        # Fire observation callback for GT collection
        await self._on_episode_observation(episode, obs)

        # Record trajectory step
        step = TrajectoryStep(
            step_num=episode.current_step - 1,
            observation=episode.last_observation,
            action=parsed_action,
            action_result=action_result,
            prompt=self._format_observation(episode, episode.last_observation),
            raw_response=action,
        )
        episode.trajectory.append(step)
        episode.last_observation = obs

        # Calculate step-wise reward
        step_reward = 0.0
        reward_breakdown = RewardBreakdown()
        if episode.reward_calculator:
            # Determine if URL was blocked
            is_blocked = "blocked" in action_result.lower() if action_result else False

            # Get collected asset IDs for progress tracking
            collected_ids = set(episode.gt_collector.get_collected_api_data().keys())

            reward_breakdown = episode.reward_calculator.calculate_step_reward(
                url=obs.url if obs else "",
                action_result=action_result,
                collected_asset_ids=collected_ids,
                is_blocked=is_blocked,
                parse_failed=False,
            )
            step_reward = reward_breakdown.total
            episode.cumulative_reward += step_reward
            episode.reward_history.append(reward_breakdown)

        # Check max steps
        if episode.current_step >= episode.max_steps:
            episode.truncated = True
            episode.done = True
            episode.failure_reason = "max_steps_reached"
            log("Actor", f"Episode {episode_id[:8]}... truncated at max_steps={episode.max_steps}")

        # Build new observation
        observation = self._format_observation(episode, obs)

        return OpenEnvResponse(
            observation=observation,
            episode_id=episode_id,
            reward=step_reward,
            done=episode.done,
            truncated=episode.truncated,
            info=self._build_info(episode, reward_breakdown=reward_breakdown),
        )

    async def state(self, episode_id: Optional[str] = None) -> OpenEnvResponse:
        """
        Get current state without advancing.

        Args:
            episode_id: Episode identifier

        Returns:
            OpenEnvResponse with current state
        """
        if not episode_id:
            return OpenEnvResponse(
                observation="No episode_id provided.",
                done=True,
                truncated=True,
                info={"error": {"type": "no_episode_id"}},
            )

        episode = self._episodes.get(episode_id)
        if not episode:
            return OpenEnvResponse(
                observation=f"Episode {episode_id} not found.",
                done=True,
                truncated=True,
                info={"error": {"type": "episode_not_found"}},
            )

        observation = self._format_observation(episode, episode.last_observation)

        return OpenEnvResponse(
            observation=observation,
            episode_id=episode_id,
            done=episode.done,
            truncated=episode.truncated,
            info=self._build_info(episode),
        )

    async def stop(self, episode_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Stop and cleanup an episode.

        Args:
            episode_id: Episode identifier

        Returns:
            Dict with cleanup status and final metrics
        """
        if not episode_id:
            return {"status": "ok", "stopped": False, "message": "No episode_id provided"}

        episode = self._episodes.pop(episode_id, None)
        if not episode:
            return {"status": "ok", "stopped": False, "episode_id": episode_id}

        # Clean up GT collector reference
        set_current_gt_collector(None)

        # Gather final metrics before cleanup
        elapsed = time.time() - episode.start_time
        interceptor_stats = episode.interceptor.get_stats() if episode.interceptor else {}

        # Clean up memory to prevent leaks
        if episode.gt_collector:
            episode.gt_collector.cleanup()
        if episode.interceptor:
            episode.interceptor.cleanup()
        if episode.cached_pages:
            episode.cached_pages.clear()

        # Close browser session
        try:
            await episode.session.close()
        except Exception as e:
            log("Actor", f"Error closing session for episode {episode_id[:8]}...: {e}")

        return {
            "status": "ok",
            "stopped": True,
            "episode_id": episode_id,
            "metrics": {
                "steps": episode.current_step,
                "max_steps": episode.max_steps,
                "elapsed_seconds": elapsed,
                "done": episode.done,
                "truncated": episode.truncated,
                "failure_reason": episode.failure_reason,
                "final_answer": episode.final_answer,
                "cache_stats": interceptor_stats,
            },
        }

    # ========== OpenEnv Helper Methods ==========

    def _format_observation(
        self,
        episode: EpisodeState,
        obs: BrowserObservation,
        is_initial: bool = False,
    ) -> str:
        """Format observation as a string for the agent."""
        if is_initial:
            # Include system prompt for initial observation
            return f"{episode.system_prompt}\n\n---\n\n{self._format_step_prompt(episode, obs)}"
        else:
            return self._format_step_prompt(episode, obs)

    def _format_step_prompt(self, episode: EpisodeState, obs: BrowserObservation) -> str:
        """Format step prompt with current observation and history."""
        return episode.policy.build_step_prompt(
            obs,
            episode.trajectory,
            episode.current_step + 1,
            episode.max_steps,
        )

    def _build_info(
        self,
        episode: Optional[EpisodeState],
        error: Optional[Dict[str, Any]] = None,
        reward_breakdown: Optional[RewardBreakdown] = None,
    ) -> Dict[str, Any]:
        """Build info dict for OpenEnvResponse."""
        info: Dict[str, Any] = {}

        if episode:
            info["task_id"] = episode.task_id
            info["seed"] = episode.seed
            info["current_step"] = episode.current_step
            info["max_steps"] = episode.max_steps
            info["num_subtasks"] = len(episode.task.subtasks)
            if episode.last_observation:
                info["current_url"] = episode.last_observation.url
            if episode.failure_reason:
                info["failure_reason"] = episode.failure_reason
            if episode.final_answer:
                info["final_answer"] = episode.final_answer

            # Add reward tracking info
            info["cumulative_reward"] = episode.cumulative_reward
            if episode.reward_calculator:
                info["reward_state"] = episode.reward_calculator.get_state()

        if reward_breakdown:
            info["reward_breakdown"] = reward_breakdown.to_dict()

        if error:
            info["error"] = error

        return info

    async def _on_episode_navigation(self, episode: EpisodeState, url: str):
        """Handle navigation event for caching."""
        if not self.use_cache:
            return

        normalized = normalize_url(url)
        if normalized in episode.cached_pages:
            return

        # Find plugin for this URL (includes external URL support)
        plugin = None
        for p in episode.plugins_used.values():
            for domain in p.allowed_domains:
                if _url_matches_domain(url, domain):
                    plugin = p
                    break
            if plugin:
                break
        # Check dynamic URL validation (for external navigation)
        if not plugin:
            for p in episode.plugins_used.values():
                if hasattr(p, 'is_url_allowed') and p.is_url_allowed(url):
                    plugin = p
                    break

        if not plugin:
            return

        try:
            # Check if this page needs API data
            need_api = plugin.needs_api_data(url)
            page_req = PageRequirement.data(url) if need_api else PageRequirement.nav(url)

            # Fetch and cache the page
            pages = await self.cache_manager.ensure_cached([page_req], plugin)
            episode.cached_pages.update(pages)
            req_type = "data" if need_api else "nav"
            log("Actor", f"Cached ({req_type}): {url[:55]}...")

            # For plugins with external URL support, register external URLs
            # from cached api_data (needed when cache HIT bypasses fetch_api_data)
            if hasattr(plugin, '_extract_external_urls'):
                for cached_page in pages.values():
                    if cached_page.api_data:
                        plugin._extract_external_urls(cached_page.api_data)
        except CacheFatalError:
            raise
        except Exception as e:
            log("Actor", f"Navigation cache error: {e}")

    async def _on_episode_observation(self, episode: EpisodeState, obs: BrowserObservation):
        """Handle observation event for GT collection."""
        if not obs or not obs.url or obs.url == "about:blank":
            return

        url = obs.url
        api_data = None

        if self.use_cache:
            # CACHE mode: use cached api_data
            normalized = normalize_url(url)
            cached_page = episode.cached_pages.get(normalized)
            if cached_page:
                api_data = cached_page.api_data
        else:
            # LIVE mode: fetch api_data from network
            # Find plugin for this URL (includes external URL support)
            plugin = None
            for p in episode.plugins_used.values():
                for domain in p.allowed_domains:
                    if _url_matches_domain(url, domain):
                        plugin = p
                        break
                if plugin:
                    break
            # Check dynamic URL validation (for external navigation)
            if not plugin:
                for p in episode.plugins_used.values():
                    if hasattr(p, 'is_url_allowed') and p.is_url_allowed(url):
                        plugin = p
                        break

            if plugin:
                need_api = plugin.needs_api_data(url)
                if need_api:
                    # Data page: API fetch must succeed
                    try:
                        api_data = await plugin.fetch_api_data(url)
                    except Exception as e:
                        raise CacheFatalError(
                            f"LIVE mode API fetch failed (GT invalid): {e}",
                            url=url,
                        )
                    if not api_data:
                        raise CacheFatalError(
                            f"LIVE mode API returned empty data (GT invalid)",
                            url=url,
                        )

        # Collect GT from this page visit
        await episode.gt_collector.on_page_visit(
            url,
            obs.accessibility_tree,
            api_data=api_data,
        )
