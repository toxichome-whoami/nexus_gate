import asyncio
import time
from typing import Optional, Set

import httpx
import structlog

from config.provider import GlobalConfigProvider
from webhook.emitter import WebhookQueueList
from webhook.signer import generate_signature

logger = structlog.get_logger()

# Shared async client for connection pooling
_client: Optional[httpx.AsyncClient] = None

# Track retry tasks to cancel on shutdown — prevents orphaned coroutines
_retry_tasks: Set[asyncio.Task] = set()

# ─────────────────────────────────────────────────────────────────────────────
# Internal Subsystems
# ─────────────────────────────────────────────────────────────────────────────


def _get_client() -> httpx.AsyncClient:
    """Resolves or instantiates the singleton connection pool for webhooks."""
    global _client
    if _client is None:
        config = GlobalConfigProvider().get_config()
        _client = httpx.AsyncClient(timeout=config.webhooks.timeout)
    return _client


def _schedule_retry(queue: asyncio.Queue, task: dict, delay_sec: int):
    """Forks a non-blocking coroutine to re-queue a failed dispatch after delay."""

    async def retry_routine():
        await asyncio.sleep(delay_sec)
        task["attempt"] = task.get("attempt", 1) + 1
        try:
            queue.put_nowait(task)
        except asyncio.QueueFull:
            logger.error("Webhook queue full during retry, dropping")

    t = asyncio.create_task(retry_routine())
    _retry_tasks.add(t)
    t.add_done_callback(_retry_tasks.discard)


async def _process_dispatch_task(
    task: dict, queue: asyncio.Queue, client: httpx.AsyncClient, config
):
    """Formats, signs, and executes an individual HTTP transmission block."""
    hook_name = task["hook_name"]
    url = task["url"]
    secret = task["secret"]
    headers = task.get("headers", {})
    payload = task["payload"]
    attempt = task.get("attempt", 1)

    signature = generate_signature(secret, payload)

    request_headers = {
        "Content-Type": "application/json",
        config.webhooks.secret_header: signature,
        "X-NexusGate-Timestamp": str(int(time.time())),
        **headers,
    }

    try:
        response = await client.post(url, content=payload, headers=request_headers)
        response.raise_for_status()
        logger.debug("Webhook delivered successfully", hook=hook_name, url=url)
    except Exception as network_error:
        _handle_dispatch_failure(
            queue, task, attempt, hook_name, url, network_error, config
        )


def _handle_dispatch_failure(
    queue: asyncio.Queue,
    task: dict,
    attempt: int,
    hook_name: str,
    url: str,
    error: Exception,
    config,
):
    """Determines retry eligibility based on configuration limits."""
    max_retries = config.webhooks.max_retries

    if attempt > max_retries:
        logger.error(
            "Webhook max retries exceeded, dropping",
            hook=hook_name,
            url=url,
            error=str(error),
        )
        return

    delay_sec = config.webhooks.retry_delay**attempt
    logger.warning(
        "Webhook delivery failed, scheduling retry",
        hook=hook_name,
        attempt=attempt,
        max_retries=max_retries,
        delay_sec=delay_sec,
        error=str(error),
    )
    _schedule_retry(queue, task, delay_sec)


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle & Entrypoint
# ─────────────────────────────────────────────────────────────────────────────


async def webhook_shutdown():
    """Cancels all pending retry tasks and closes the HTTP client.
    Called from lifespan teardown to prevent resource leaks."""
    global _client

    # Cancel all pending retry tasks
    for task in list(_retry_tasks):
        task.cancel()
    _retry_tasks.clear()

    # Close the HTTP client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Webhook HTTP client closed")


async def dispatcher_worker():
    """Background Daemon consuming in-memory queues and dispatching payloads."""
    logger.info("Webhook dispatcher started")
    queue = WebhookQueueList.get_queue()
    client = _get_client()
    config = GlobalConfigProvider().get_config()

    try:
        while True:
            try:
                task = await queue.get()
                try:
                    await _process_dispatch_task(task, queue, client, config)
                finally:
                    queue.task_done()

            except asyncio.CancelledError:
                raise  # Let the outer try/finally handle cleanup

            except Exception as system_error:
                logger.error(
                    "Dispatcher encountered unexpected error",
                    error=str(system_error),
                )
                await asyncio.sleep(
                    1
                )  # Prevent CPU spinning on catastrophic iteration fail
    except asyncio.CancelledError:
        logger.info("Webhook dispatcher shutting down")
    finally:
        await webhook_shutdown()
