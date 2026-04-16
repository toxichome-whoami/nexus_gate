import asyncio
import httpx
import structlog
import time
from typing import Dict, Any

from config.loader import ConfigManager
from webhook.emitter import WebhookQueueList
from webhook.signer import generate_signature

logger = structlog.get_logger()

# Shared async client for connection pooling
_client = None

# ─────────────────────────────────────────────────────────────────────────────
# Internal Subsystems
# ─────────────────────────────────────────────────────────────────────────────

def _get_client() -> httpx.AsyncClient:
    """Resolves or instantiates the singleton connection pool for webhooks."""
    global _client
    if _client is None:
        config = ConfigManager.get()
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
            
    asyncio.create_task(retry_routine())

async def _process_dispatch_task(task: dict, queue: asyncio.Queue, client: httpx.AsyncClient, config):
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
        **headers
    }
    
    try:
        response = await client.post(url, content=payload, headers=request_headers)
        response.raise_for_status()
        logger.debug("Webhook delivered successfully", hook=hook_name, url=url)
    except Exception as network_error:
        _handle_dispatch_failure(queue, task, attempt, hook_name, url, network_error, config)

def _handle_dispatch_failure(queue: asyncio.Queue, task: dict, attempt: int, hook_name: str, url: str, error: Exception, config):
    """Determines retry eligibility based on configuration limits."""
    max_retries = config.webhooks.max_retries
    
    if attempt > max_retries:
        logger.error("Webhook max retries exceeded, dropping", hook=hook_name, url=url, error=str(error))
        return
        
    delay_sec = config.webhooks.retry_delay ** attempt
    logger.warning(
        "Webhook delivery failed, scheduling retry", 
        hook=hook_name, attempt=attempt, max_retries=max_retries, 
        delay_sec=delay_sec, error=str(error)
    )
    _schedule_retry(queue, task, delay_sec)

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint Worker
# ─────────────────────────────────────────────────────────────────────────────

async def dispatcher_worker():
    """Background Daemon consuming in-memory queues and dispatching payloads."""
    logger.info("Webhook dispatcher started")
    queue = WebhookQueueList.get_queue()
    client = _get_client()
    config = ConfigManager.get()
    
    while True:
        try:
            task = await queue.get()
            try:
                await _process_dispatch_task(task, queue, client, config)
            finally:
                queue.task_done()
                
        except asyncio.CancelledError:
            logger.info("Webhook dispatcher shutting down")
            if _client:
                await _client.aclose()
            break
            
        except Exception as system_error:
            logger.error("Dispatcher encountered unexpected error", error=str(system_error))
            await asyncio.sleep(1) # Prevent CPU spinning on catastrophic iteration fail
