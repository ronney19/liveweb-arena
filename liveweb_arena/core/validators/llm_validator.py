"""LLM-based answer validator for flexible answer matching"""

from dataclasses import dataclass
from typing import Any, List
import json
import os
import re

from ...utils.logger import log


@dataclass
class LLMValidationResult:
    """Result from LLM validation"""
    score: float  # 0.0 - 1.0
    is_correct: bool
    expected: Any
    actual: Any
    reasoning: str  # LLM's reasoning (max 50 words)


# Common validation prompt (shared by all task types)
COMMON_VALIDATION_PROMPT = """You are an answer validator. Compare the expected answer (ground truth from API) with the actual answer (from agent).

Question: {question}
Expected Answer (Ground Truth): {expected}
Actual Answer (Agent Response): {actual}

{task_specific_rules}

General Rules:
1. Be flexible with format differences (e.g., "28°C" = "28 degrees" = "28")
2. If agent says data unavailable but expected has a value: score 0.0
3. Output ONLY a JSON object: {{"score": <float 0.0 to 1.0>, "reasoning": "<brief max 30 words>"}}
"""

# Default task-specific rules (used when template doesn't provide any)
DEFAULT_TASK_RULES = """Task-Specific Rules:
- Score 1.0: Answers match exactly (ignoring format)
- Score 0.0: Answers do not match"""

# Note: NO_GROUND_TRUTH_PROMPT removed - ground truth is always required

# Validation models tried in order. If one fails, the next is used.
# These are defaults for the original provider stack.
VALIDATION_MODELS: List[str] = [
    "openai/gpt-oss-120b-TEE",
    "Qwen/Qwen3-235B-A22B-Instruct-2507-TEE",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen3-32B",
]

OPENAI_VALIDATION_MODELS: List[str] = [
    "gpt-4",
    "gpt-3.5-turbo",
]


def _get_validation_models(llm_client) -> List[str]:
    """
    Resolve validation models from environment or provider-specific defaults.

    Priority:
    1. VALIDATION_MODELS env var (comma-separated)
    2. OpenAI-safe defaults when base_url points to api.openai.com
    3. Project default VALIDATION_MODELS
    """
    env_models = os.getenv("VALIDATION_MODELS", "")
    if env_models.strip():
        models = [m.strip() for m in env_models.split(",") if m.strip()]
        if models:
            return models

    base_url = str(getattr(llm_client, "_base_url", "")).lower()
    if "api.openai.com" in base_url:
        return OPENAI_VALIDATION_MODELS

    return VALIDATION_MODELS


class LLMValidator:
    """
    LLM-based validator for flexible answer matching.

    Uses an LLM to judge if the actual answer matches the expected answer,
    allowing for format variations and minor differences.
    """

    def __init__(self, llm_client):
        """
        Initialize LLM validator.

        Args:
            llm_client: LLM client for making validation calls
        """
        self._llm_client = llm_client

    async def validate(
        self,
        question: str,
        expected: Any,
        actual: Any,
        task_specific_rules: str = "",
        temperature: float = 0.0,
    ) -> LLMValidationResult:
        """
        Validate answer using LLM with automatic model fallback.

        Tries each model in VALIDATION_MODELS in order. If one model fails
        (503, rate limit, timeout, etc.), the next model is tried.

        Args:
            question: The original question/task
            expected: Expected answer (ground truth)
            actual: Actual answer from the agent
            task_specific_rules: Task-specific validation rules from template
            temperature: LLM temperature (0 for deterministic)

        Returns:
            LLMValidationResult with score and reasoning
        """
        # Handle None values
        if actual is None or actual == "":
            return LLMValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=actual,
                reasoning="No answer provided by the agent.",
            )

        # Ground truth is required - cannot validate without it
        if expected is None:
            return LLMValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=actual,
                reasoning="Ground truth unavailable - cannot validate answer.",
            )

        # Use task-specific rules or default
        rules = task_specific_rules if task_specific_rules else DEFAULT_TASK_RULES

        # Normal validation with ground truth
        prompt = COMMON_VALIDATION_PROMPT.format(
            question=question,
            expected=str(expected),
            actual=str(actual),
            task_specific_rules=rules,
        )

        # Try each validation model in order
        validation_models = _get_validation_models(self._llm_client)
        last_error = None
        for model in validation_models:
            try:
                response, _ = await self._llm_client.chat(
                    system="You are a precise answer validator. Output only valid JSON.",
                    user=prompt,
                    model=model,
                    temperature=temperature,
                )

                result = self._parse_response(response)

                return LLMValidationResult(
                    score=result["score"],
                    is_correct=result["score"] >= 0.8,
                    expected=expected,
                    actual=actual,
                    reasoning=result["reasoning"],
                )

            except Exception as e:
                last_error = e
                log("Validator", f"Model {model} failed: {e}, trying next")
                continue

        # All models exhausted
        raise RuntimeError(
            f"All validation models failed. Last error: {last_error}"
        ) from last_error

    def _parse_response(self, response: str) -> dict:
        """Parse LLM response to extract score and reasoning"""
        # Try direct JSON parse
        try:
            data = json.loads(response.strip())
            return self._validate_result(data)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from response
        json_match = re.search(r'\{[^{}]*\}', response)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return self._validate_result(data)
            except json.JSONDecodeError:
                pass

        # Fallback: try to extract score and reasoning manually
        score_match = re.search(r'"?score"?\s*:\s*([0-9.]+)', response)
        reasoning_match = re.search(r'"?reasoning"?\s*:\s*"([^"]+)"', response)

        if not score_match:
            raise ValueError(f"Cannot parse LLM validation response: {response[:200]}")

        score = float(score_match.group(1))
        reasoning = reasoning_match.group(1) if reasoning_match else "Score extracted but reasoning unparseable"

        return {"score": min(1.0, max(0.0, score)), "reasoning": reasoning}

    def _validate_result(self, data: dict) -> dict:
        """Validate and normalize parsed result"""
        score = float(data["score"])
        score = min(1.0, max(0.0, score))  # Clamp to [0, 1]

        reasoning = str(data["reasoning"])
        # Truncate reasoning to ~50 words
        words = reasoning.split()
        if len(words) > 50:
            reasoning = " ".join(words[:50]) + "..."

        return {"score": score, "reasoning": reasoning}


async def validate_answers_with_llm(
    llm_client,
    subtasks: list,
    answers: dict,
    ground_truths: dict,
    validation_rules: dict = None,
    parallel: bool = True,
) -> list:
    """
    Validate multiple answers using LLM with automatic model fallback.

    Models are defined in VALIDATION_MODELS and tried in order per validation call.

    Args:
        llm_client: LLM client for validation calls
        subtasks: List of SubTask objects
        answers: Dict of answer_tag -> answer from agent
        ground_truths: Dict of answer_tag -> ground truth value
        validation_rules: Dict of answer_tag -> task-specific validation rules
        parallel: Whether to validate answers in parallel (default: True)

    Returns:
        List of validation result dicts with expected, actual, score, reasoning
    """
    import asyncio

    validator = LLMValidator(llm_client)
    validation_rules = validation_rules or {}

    async def validate_single(subtask):
        """Validate a single subtask answer"""
        tag = subtask.answer_tag
        question = subtask.intent
        expected = ground_truths.get(tag)
        actual = answers.get(tag)
        task_rules = validation_rules.get(tag, "")

        result = await validator.validate(
            question=question,
            expected=expected,
            actual=actual,
            task_specific_rules=task_rules,
        )

        return {
            "question": question,
            "answer_tag": tag,
            "expected": result.expected,
            "actual": result.actual,
            "score": result.score,
            "is_correct": result.is_correct,
            "reasoning": result.reasoning,
        }

    if parallel and len(subtasks) > 1:
        # Parallel validation for multiple subtasks
        tasks = [validate_single(subtask) for subtask in subtasks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Any validation failure = evaluation invalid (can't score reliably)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                subtask = subtasks[i]
                raise RuntimeError(
                    f"Validation failed for '{subtask.answer_tag}': {result}"
                ) from result

        return results
    else:
        # Sequential validation
        results = []
        for subtask in subtasks:
            result = await validate_single(subtask)
            results.append(result)
        return results
