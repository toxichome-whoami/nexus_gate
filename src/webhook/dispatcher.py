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

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        config = ConfigManager.get()
        _client = httpx.AsyncClient(timeout=config.webhooks.timeout)
    return _client

async def dispatcher_worker():
    """Background task to take items from the queue and send them via HTTP."""
    logger.info("Webhook dispatcher started")
    queue = WebhookQueueList.get_queue()
    client = get_client()
    config = ConfigManager.get()
    
    max_retries = config.webhooks.max_retries
    retry_delay_base = config.webhooks.retry_delay
    secret_header = config.webhooks.secret_header
    
    while True:
        try:
            task = await queue.get()
            
            # Unpack task
            hook_name = task["hook_name"]
            url = task["url"]
            secret = task["secret"]
            headers = task.get("headers", {})
            payload = task["payload"]
            attempt = task.get("attempt", 1)
            
            # Generate signature and prepare headers
            sig = generate_signature(secret, payload)
            req_headers = {
                "Content-Type": "application/json",
                secret_header: sig,
                "X-NexusGate-Timestamp": str(int(time.time())),
                **headers
            }
            
            # Send the request
            try:
                response = await client.post(url, content=payload, headers=req_headers)
                response.raise_for_status()
                logger.debug("Webhook delivered successfully", hook=hook_name, url=url)
            except Exception as e:
                # Failed, schedule retry if under max_retries
                if attempt <= max_retries:
                    delay = retry_delay_base ** attempt
                    logger.warning(
                        "Webhook delivery failed, scheduling retry", 
                        hook=hook_name, attempt=attempt, max_retries=max_retries, 
                        delay_sec=delay, error=str(e)
                    )
                    
                    # Schedule retry without blocking the dispatcher
                    async def retry_task(t, d):
                        await asyncio.sleep(d)
                        t["attempt"] = t.get("attempt", 1) + 1
                        try:
                            queue.put_nowait(t)
                        except asyncio.QueueFull:
                            logger.error("Webhook queue full during retry, dropping")
                            
                    asyncio.create_task(retry_task(task, delay))
                else:
                    logger.error("Webhook max retries exceeded, dropping", hook=hook_name, url=url, error=str(e))
            finally:
                queue.task_done()
                
        except asyncio.CancelledError:
            logger.info("Webhook dispatcher shutting down")
            if _client:
                await _client.aclose()
            break
        except Exception as e:
            logger.error("Dispatcher encountered unexpected error", error=str(e))
            await asyncio.sleep(1) # Prevent tight loop on persistent error
