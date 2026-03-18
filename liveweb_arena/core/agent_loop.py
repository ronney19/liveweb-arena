"""Agent loop for browser-based task execution"""

import asyncio
from typing import Any, Callable, List, Optional, Tuple

from .browser import BrowserSession
from .cache import CacheFatalError
from .models import BrowserAction, CompositeTask, TrajectoryStep
from .agent_protocol import AgentProtocol
from ..utils.llm_client import LLMClient, LLMFatalError
from ..utils.logger import log


class BrowserFatalError(Exception):
    """
    Raised when browser navigation fails after maximum retries.

    This indicates persistent network or site accessibility issues
    that should terminate evaluation immediately.
    """

    def __init__(self, message: str, url: str = None, attempts: int = 0):
        super().__init__(message)
        self.url = url
        self.attempts = attempts


# Type for navigation callback: async (url: str) -> None
NavigationCallback = Callable[[str], Any]
# Type for step complete callback: async (step: TrajectoryStep) -> None
StepCompleteCallback = Callable[["TrajectoryStep"], Any]
# Type for observation callback: async (observation: BrowserObservation) -> None
ObservationCallback = Callable[[Any], Any]

# URL patterns that indicate browser/network errors (not AI's fault)
# Note: about:blank is NOT an error - it's the initial page where AI starts
ERROR_URL_PATTERNS = [
    "chrome-error://",
    "about:neterror",
]


def is_error_page(url: str) -> bool:
    """Check if URL indicates a browser error (not AI's fault).

    Note: about:blank is NOT considered an error page - it's the starting point.
    Only actual error pages like chrome-error:// are treated specially.
    """
    if not url:
        return False
    return any(pattern in url.lower() for pattern in ERROR_URL_PATTERNS)


class AgentLoop:
    """
    Main agent loop that drives browser interaction via LLM.

    Uses AgentProtocol (function calling) for structured tool_calls interaction.
    The loop maintains trajectory state internally for partial recovery on timeout.
    """

    def __init__(
        self,
        session: BrowserSession,
        llm_client: LLMClient,
        protocol: AgentProtocol,
        max_steps: int = 30,
        on_navigation: Optional[NavigationCallback] = None,
        on_step_complete: Optional[StepCompleteCallback] = None,
        on_observation: Optional[ObservationCallback] = None,
    ):
        self._session = session
        self._llm_client = llm_client
        self._protocol = protocol
        self._max_steps = max_steps
        self._on_navigation = on_navigation
        self._on_step_complete = on_step_complete
        self._on_observation = on_observation

        # Internal state for partial recovery
        self._trajectory: List[TrajectoryStep] = []
        self._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._final_answer = None

    def get_trajectory(self) -> List[TrajectoryStep]:
        """Get current trajectory (for partial recovery on timeout)"""
        return self._trajectory.copy()

    def get_usage(self) -> Optional[dict]:
        """Get current usage stats"""
        return self._total_usage.copy() if any(self._total_usage.values()) else None

    def get_final_answer(self) -> Any:
        """Get final answer if available"""
        return self._final_answer

    async def _call_llm(
        self, system_prompt: str, user_prompt: str, model: str,
        temperature: float, seed: Optional[int],
    ) -> Tuple[str, Optional[BrowserAction], Optional[dict]]:
        """
        Call LLM with function calling protocol.

        Returns:
            Tuple of (raw_response, parsed_action_or_None, usage)
        """
        tools = self._protocol.get_tools()
        response = await self._llm_client.chat_with_tools(
            system=system_prompt,
            user=user_prompt,
            model=model,
            tools=tools,
            temperature=temperature,
            seed=seed,
        )
        raw_response = response.content
        if response.has_tool_calls:
            tc = response.tool_calls[0]
            raw_response = raw_response or f"[tool_call: {tc.function['name']}({tc.function['arguments']})]"
        action = self._protocol.parse_response(raw_response, response.tool_calls)
        return raw_response, action, response.usage

    async def run(
        self,
        task: CompositeTask,
        model: str,
        temperature: float = 0.7,
        seed: Optional[int] = None,
    ) -> Tuple[List[TrajectoryStep], Any, Optional[dict]]:
        """
        Run the agent loop until completion or max_steps.

        Args:
            task: Composite task to complete
            model: LLM model name
            temperature: LLM temperature
            seed: LLM seed for reproducibility

        Returns:
            Tuple of (trajectory, final_answer, usage)
            - trajectory: List of TrajectoryStep
            - final_answer: The final answer dict from stop action, or None
            - usage: Aggregated LLM usage dict
        """
        # Reset internal state
        self._trajectory = []
        self._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._final_answer = None
        self._max_steps_reached = False
        self._parse_failed = False

        system_prompt = self._protocol.build_system_prompt(task)
        log("Agent", f"Starting loop, max_steps={self._max_steps}, protocol=function_calling")

        obs = await self._session.goto("about:blank")
        consecutive_errors = 0
        consecutive_error_pages = 0
        max_error_page_retries = 10  # Prevent infinite loops on persistent network issues

        effective_step = 0  # Count all steps including error pages (AI sees them)
        iteration = 0  # Total iterations (safety limit)
        last_goto_url = None  # Track last navigation URL for error context

        while effective_step < self._max_steps:
            iteration += 1
            # Safety limit to prevent infinite loops
            if iteration > self._max_steps * 3:
                log("Agent", "Too many iterations, breaking loop", force=True)
                break

            # Check if we're on an error page - let AI see it and decide what to do
            if is_error_page(obs.url):
                consecutive_error_pages += 1
                log("Agent", f"Error page detected (visible to AI): {obs.url[:50]}")

                # Safety limit: if AI keeps landing on error pages, eventually stop
                if consecutive_error_pages >= max_error_page_retries:
                    log("Agent", f"Too many consecutive error pages ({consecutive_error_pages}), AI unable to navigate", force=True)
                    raise BrowserFatalError(
                        f"AI unable to navigate after {consecutive_error_pages} consecutive error pages",
                        url=last_goto_url,
                        attempts=consecutive_error_pages,
                    )
                # Error pages count as a step - AI will see it and can take corrective action
            else:
                # Reset error page counter on valid page
                consecutive_error_pages = 0

            effective_step += 1
            log("")  # Blank line between steps
            log("Agent", f"Step {effective_step}/{self._max_steps}, url={obs.url[:50]}")

            # Fire observation callback for real-time GT collection (before action)
            if self._on_observation:
                try:
                    await self._on_observation(obs)
                except CacheFatalError:
                    raise
                except Exception as e:
                    log("Agent", f"Observation callback error: {e}")
                    # Record GT collection failure so it's visible in results
                    from .gt_collector import get_current_gt_collector
                    gt = get_current_gt_collector()
                    if gt:
                        gt.record_observation_error(obs.url, str(e))

            # Pre-save observation so it's not lost if LLM call times out
            current_obs = obs
            step_num = effective_step - 1  # 0-indexed step number for trajectory
            user_prompt = self._protocol.build_step_prompt(
                current_obs, self._trajectory, effective_step, self._max_steps
            )

            try:
                raw_response, action, usage = await self._call_llm(
                    system_prompt, user_prompt, model, temperature, seed,
                )
                if usage:
                    for key in self._total_usage:
                        self._total_usage[key] += usage.get(key, 0)
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                max_consecutive = 3
                log("Agent", f"LLM error ({consecutive_errors}/{max_consecutive}): {type(e).__name__}: {e}", force=True)

                if consecutive_errors >= max_consecutive:
                    raise LLMFatalError(
                        f"LLM errors exhausted after {consecutive_errors} consecutive failures: {type(e).__name__}: {e}",
                        original_error=e,
                        attempts=consecutive_errors,
                    )

                # Brief wait before retry
                await asyncio.sleep(1)
                continue

            # Parse failed - terminate immediately
            if action is None:
                log("Agent", f"PARSE FAILED: {raw_response[:200]!r}", force=True)

                step = TrajectoryStep(
                    step_num=step_num,
                    observation=current_obs,
                    action=None,
                    action_result="Parse failed - model output not valid JSON",
                    prompt=user_prompt,
                    raw_response=raw_response,
                )
                self._trajectory.append(step)
                self._parse_failed = True
                break

            if action.action_type == "stop":
                final_params = action.params.get("final", {})
                self._final_answer = final_params if final_params else action.params
                log("Agent", f"Completed: {self._final_answer}")

                step = TrajectoryStep(
                    step_num=step_num,
                    observation=current_obs,
                    action=action,
                    action_result="Task completed",
                    prompt=user_prompt,
                    raw_response=raw_response,
                )
                self._trajectory.append(step)

                # Fire step complete callback for final step
                if self._on_step_complete:
                    try:
                        await self._on_step_complete(step)
                    except Exception as e:
                        log("Agent", f"Step complete callback error: {e}")
                break
            else:
                log("Agent", f"Action: {action.action_type}")
                old_url = obs.url if obs else None

                # Execute action - browser handles navigation errors internally
                # and returns error pages as valid observations
                try:
                    obs = await self._session.execute_action(action)
                    action_result = "Success"

                    # Track goto URL for error context
                    if action.action_type == "goto":
                        last_goto_url = action.params.get("url", "")

                    # Fire navigation callback if URL changed
                    if self._on_navigation and obs.url != old_url:
                        try:
                            await self._on_navigation(obs.url)
                        except CacheFatalError:
                            raise  # Cache failure = browser can't load = terminate immediately
                        except Exception as e:
                            log("Agent", f"Navigation callback error: {e}")
                except Exception as e:
                    # Non-navigation action failed
                    action_result = f"Failed: {e}"

            step = TrajectoryStep(
                step_num=step_num,
                observation=current_obs,
                action=action,
                action_result=action_result,
                prompt=user_prompt,
                raw_response=raw_response,
            )
            self._trajectory.append(step)

            # Fire step complete callback (after action executed)
            if self._on_step_complete:
                try:
                    await self._on_step_complete(step)
                except Exception as e:
                    log("Agent", f"Step complete callback error: {e}")

        # Check if max steps reached without completion
        if self._final_answer is None and effective_step >= self._max_steps:
            self._max_steps_reached = True
            log("Agent", f"Max steps ({self._max_steps}) reached without completion", force=True)

        log("Agent", f"Finished with {len(self._trajectory)} steps")
        return self._trajectory, self._final_answer, self.get_usage()

    def is_max_steps_reached(self) -> bool:
        """Check if max steps was reached without completion"""
        return self._max_steps_reached

    def is_parse_failed(self) -> bool:
        """Check if evaluation terminated due to parse failure"""
        return self._parse_failed
