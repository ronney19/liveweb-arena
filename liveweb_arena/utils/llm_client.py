"""OpenAI-compatible LLM client with retry and streaming support"""

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import httpx
import openai

from .logger import log, progress, progress_done, is_verbose


class LLMFatalError(Exception):
    """
    Raised when LLM errors exhaust all retries.

    This indicates an unrecoverable error that should terminate evaluation
    immediately rather than continuing with degraded results.
    """

    def __init__(self, message: str, original_error: Exception = None, attempts: int = 0):
        super().__init__(message)
        self.original_error = original_error
        self.attempts = attempts


@dataclass
class ToolCall:
    """Parsed tool call from LLM response."""
    id: str
    function: dict  # {"name": str, "arguments": str}


@dataclass
class LLMResponse:
    """Structured LLM response supporting both text and tool_calls."""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Optional[dict] = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient:
    """
    OpenAI-compatible LLM client.

    Features:
    - Streaming support with usage tracking
    - Exponential backoff retry for recoverable errors
    - Configurable timeouts
    """

    # Recoverable error status codes
    RETRY_STATUS_CODES = {429, 503, 502, 500}

    # Retry configuration
    MAX_RETRIES = 10  # Increased for rate limit resilience
    BASE_DELAY = 1.0  # seconds
    MAX_DELAY = 30.0  # seconds

    # Default timeout per request (should be less than total eval timeout)
    DEFAULT_TIMEOUT = 600  # seconds
    # Maximum chunks to receive (safety limit, ~32k chunks ≈ ~64k tokens)
    MAX_CHUNKS = 32000

    def __init__(self, base_url: str, api_key: str, default_timeout: int = None):
        """
        Initialize LLM client.

        Args:
            base_url: OpenAI-compatible API base URL
            api_key: API key for authentication
            default_timeout: Default request timeout in seconds
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_timeout = default_timeout or self.DEFAULT_TIMEOUT

    async def chat(
        self,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.7,
        seed: Optional[int] = None,
        timeout_s: int = None,
    ) -> Tuple[str, Optional[dict]]:
        """
        Make a chat completion request.

        Args:
            system: System prompt
            user: User message
            model: Model name
            temperature: Sampling temperature
            seed: Random seed for reproducibility
            timeout_s: Request timeout in seconds (default: use client default)

        Returns:
            Tuple of (response content, usage dict or None)
        """
        # Use default timeout if not specified
        actual_timeout = timeout_s if timeout_s is not None else self._default_timeout

        # Build messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        # Retry loop with exponential backoff
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                # Wrap in total timeout to prevent runaway requests
                content, usage = await asyncio.wait_for(
                    self._make_request(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        seed=seed,
                        timeout_s=actual_timeout,
                    ),
                    timeout=actual_timeout,
                )
                return content, usage

            except asyncio.TimeoutError:
                last_error = TimeoutError(f"LLM request timed out after {actual_timeout}s")
                log("LLM", f"Total timeout ({actual_timeout}s) exceeded, attempt {attempt + 1}/{self.MAX_RETRIES}")
                await self._backoff(attempt)
                continue

            except openai.RateLimitError as e:
                last_error = e
                log("LLM", f"Rate limit hit, attempt {attempt + 1}/{self.MAX_RETRIES}")
                await self._backoff(attempt)

            except openai.BadRequestError as e:
                # Check for token limit errors - these are unrecoverable
                error_msg = str(e).lower()
                if "is longer than the model" in error_msg or "context_length_exceeded" in error_msg:
                    log("LLM", f"Token limit exceeded - fatal error: {e}", force=True)
                    raise LLMFatalError(
                        f"Token limit exceeded: {e}",
                        original_error=e,
                        attempts=attempt + 1,
                    )
                # Other bad request errors - don't retry
                raise

            except openai.APIStatusError as e:
                if e.status_code in self.RETRY_STATUS_CODES:
                    last_error = e
                    log("LLM", f"API error {e.status_code}, attempt {attempt + 1}/{self.MAX_RETRIES}")
                    await self._backoff(attempt)
                else:
                    raise

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                log("LLM", f"Connection error, attempt {attempt + 1}/{self.MAX_RETRIES}: {e}")
                await self._backoff(attempt)

            except Exception as e:
                # Check for token limit in generic exceptions too
                error_msg = str(e).lower()
                if "is longer than the model" in error_msg or "context_length_exceeded" in error_msg:
                    log("LLM", f"Token limit exceeded - fatal error: {e}", force=True)
                    raise LLMFatalError(
                        f"Token limit exceeded: {e}",
                        original_error=e,
                        attempts=attempt + 1,
                    )
                last_error = e
                log("LLM", f"Error, attempt {attempt + 1}/{self.MAX_RETRIES}: {e}")
                await self._backoff(attempt)

        # All retries exhausted
        raise last_error or Exception("LLM request failed after all retries")

    async def chat_with_tools(
        self,
        system: str,
        user: str,
        model: str,
        tools: Optional[List[dict]] = None,
        temperature: float = 0.7,
        seed: Optional[int] = None,
        timeout_s: int = None,
    ) -> LLMResponse:
        """
        Make a chat completion request with optional tool/function calling support.

        When tools are provided, the LLM may respond with tool_calls instead of
        (or in addition to) text content.

        Args:
            system: System prompt
            user: User message
            model: Model name
            tools: Optional list of OpenAI-format tool definitions
            temperature: Sampling temperature
            seed: Random seed for reproducibility
            timeout_s: Request timeout in seconds

        Returns:
            LLMResponse with content, tool_calls, and usage
        """
        actual_timeout = timeout_s if timeout_s is not None else self._default_timeout

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await asyncio.wait_for(
                    self._make_request_with_tools(
                        messages=messages,
                        model=model,
                        tools=tools,
                        temperature=temperature,
                        seed=seed,
                        timeout_s=actual_timeout,
                    ),
                    timeout=actual_timeout,
                )
                return response

            except asyncio.TimeoutError:
                last_error = TimeoutError(f"LLM request timed out after {actual_timeout}s")
                log("LLM", f"Total timeout ({actual_timeout}s) exceeded, attempt {attempt + 1}/{self.MAX_RETRIES}")
                await self._backoff(attempt)
                continue

            except openai.RateLimitError as e:
                last_error = e
                log("LLM", f"Rate limit hit, attempt {attempt + 1}/{self.MAX_RETRIES}")
                await self._backoff(attempt)

            except openai.BadRequestError as e:
                error_msg = str(e).lower()
                if "is longer than the model" in error_msg or "context_length_exceeded" in error_msg:
                    raise LLMFatalError(f"Token limit exceeded: {e}", original_error=e, attempts=attempt + 1)
                raise

            except openai.APIStatusError as e:
                if e.status_code in self.RETRY_STATUS_CODES:
                    last_error = e
                    log("LLM", f"API error {e.status_code}, attempt {attempt + 1}/{self.MAX_RETRIES}")
                    await self._backoff(attempt)
                else:
                    raise

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                log("LLM", f"Connection error, attempt {attempt + 1}/{self.MAX_RETRIES}: {e}")
                await self._backoff(attempt)

            except Exception as e:
                error_msg = str(e).lower()
                if "is longer than the model" in error_msg or "context_length_exceeded" in error_msg:
                    raise LLMFatalError(f"Token limit exceeded: {e}", original_error=e, attempts=attempt + 1)
                last_error = e
                log("LLM", f"Error, attempt {attempt + 1}/{self.MAX_RETRIES}: {e}")
                await self._backoff(attempt)

        raise last_error or Exception("LLM request failed after all retries")

    async def _make_request_with_tools(
        self,
        messages: list,
        model: str,
        tools: Optional[List[dict]],
        temperature: float,
        seed: Optional[int],
        timeout_s: int,
    ) -> LLMResponse:
        """Make a single API request with optional tool calling (non-streaming for tool support)."""
        timeout_config = httpx.Timeout(
            connect=30.0, read=timeout_s, write=30.0, pool=30.0,
        )

        client = openai.AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=timeout_config,
            max_retries=0,
        )

        try:
            params = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                params["tools"] = tools
            if seed is not None:
                params["seed"] = seed

            start_time = time.time()
            response = await client.chat.completions.create(**params)
            elapsed = time.time() - start_time

            if is_verbose():
                log("LLM", f"Tool call response in {elapsed:.1f}s")

            choice = response.choices[0] if response.choices else None
            if not choice:
                raise ValueError("LLM returned no choices")

            content = choice.message.content or ""
            parsed_tool_calls = []

            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    parsed_tool_calls.append(ToolCall(
                        id=tc.id,
                        function={"name": tc.function.name, "arguments": tc.function.arguments},
                    ))

            usage = response.usage.model_dump() if response.usage else None

            if not content and not parsed_tool_calls:
                raise ValueError("LLM returned empty response (no content, no tool_calls)")

            return LLMResponse(content=content.strip(), tool_calls=parsed_tool_calls, usage=usage)
        finally:
            await client.close()

    async def _make_request(
        self,
        messages: list,
        model: str,
        temperature: float,
        seed: Optional[int],
        timeout_s: int,
    ) -> Tuple[str, Optional[dict]]:
        """Make a single API request with streaming"""
        # Use longer timeouts for connection and read
        timeout_config = httpx.Timeout(
            connect=30.0,  # Connection timeout
            read=timeout_s,  # Read timeout (for streaming)
            write=30.0,  # Write timeout
            pool=30.0,  # Pool timeout
        )

        client = openai.AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=timeout_config,
            max_retries=0,  # We handle retries ourselves
        )

        try:
            # Build request parameters
            params = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }

            if seed is not None:
                params["seed"] = seed

            # Make streaming request
            start_time = time.time()
            stream = await client.chat.completions.create(**params)

            # Collect streamed content and usage
            content_parts = []
            usage = None
            chunk_count = 0
            last_progress = 0

            async for chunk in stream:
                chunk_count += 1

                # Safety limit on chunks
                if chunk_count > self.MAX_CHUNKS:
                    log("LLM", f"Chunk limit exceeded ({self.MAX_CHUNKS}), truncating response")
                    break

                if chunk.choices and chunk.choices[0].delta.content:
                    content_parts.append(chunk.choices[0].delta.content)
                if chunk.usage:
                    usage = chunk.usage.model_dump()

                # Update progress every second
                elapsed = time.time() - start_time
                if is_verbose() and elapsed - last_progress >= 1.0:
                    last_progress = elapsed
                    progress("LLM", elapsed, timeout_s, f"chunks:{chunk_count}")

            if is_verbose() and last_progress > 0:
                progress_done("LLM", f"Done in {time.time()-start_time:.1f}s, {chunk_count} chunks")

            content = "".join(content_parts)
            if not content:
                raise ValueError(f"LLM returned empty response after {chunk_count} chunks")

            return content.strip(), usage
        finally:
            # CRITICAL: Close the client to release HTTP connections
            # Without this, connections accumulate and eventually exhaust the pool
            await client.close()

    async def _backoff(self, attempt: int):
        """Exponential backoff with jitter"""
        delay = min(
            self.BASE_DELAY * (2 ** attempt) + random.uniform(0, 1),
            self.MAX_DELAY
        )
        await asyncio.sleep(delay)
