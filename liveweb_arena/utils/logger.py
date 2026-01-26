"""Simple logging utility for LiveWeb Arena"""

import asyncio
import os
import sys
import time

# Global verbose flag (initialized from environment)
_verbose = os.environ.get("LIVEWEB_VERBOSE", "").lower() in ("1", "true")


def set_verbose(enabled: bool):
    """Enable or disable verbose logging"""
    global _verbose
    _verbose = enabled


def is_verbose() -> bool:
    """Check if verbose mode is enabled"""
    return _verbose


def log(tag: str, message: str, force: bool = False):
    """
    Print a log message if verbose mode is enabled.

    Args:
        tag: Component tag (e.g., "LLM", "Agent", "Actor")
        message: Log message
        force: Print even if verbose is disabled (for errors/warnings)
    """
    if _verbose or force:
        print(f"[{tag}] {message}", file=sys.stderr, flush=True)


def progress(tag: str, elapsed: float, timeout: float, extra: str = ""):
    """
    Print progress indicator (only in verbose mode).

    Args:
        tag: Component tag
        elapsed: Elapsed time in seconds
        timeout: Total timeout in seconds
        extra: Extra info (e.g., retry count)
    """
    if not _verbose:
        return
    bar_width = 20
    ratio = min(elapsed / timeout, 1.0)
    filled = int(bar_width * ratio)
    bar = "█" * filled + "░" * (bar_width - filled)
    msg = f"{bar} {int(elapsed)}s/{int(timeout)}s"
    if extra:
        msg += f" {extra}"
    print(f"\r[{tag}] {msg}", end="", file=sys.stderr, flush=True)


def progress_done(tag: str, message: str = ""):
    """Clear progress line and print completion message."""
    if not _verbose:
        return
    # Clear the line
    print(f"\r[{tag}] {message:<60}", file=sys.stderr, flush=True)


async def run_with_progress(
    coro,
    tag: str,
    timeout: float,
    extra_fn=None,
    interval: float = 1.0,
):
    """
    Run coroutine with progress display.

    Args:
        coro: Coroutine to run
        tag: Log tag for progress display
        timeout: Timeout value to display
        extra_fn: Optional callable returning extra info string
        interval: Progress update interval in seconds

    Returns:
        Result of the coroutine
    """
    task = asyncio.create_task(coro)
    start = time.time()

    while not task.done():
        elapsed = time.time() - start
        extra = extra_fn() if extra_fn else ""
        progress(tag, elapsed, timeout, extra)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break

    progress_done(tag)
    return await task
