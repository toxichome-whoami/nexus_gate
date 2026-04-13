"""
Metrics endpoint exposing Prometheus-compatible OpenMetrics format.
Tracks: request counts by path/method/status, active DB connections,
cache hit/miss ratios, rate limit rejections, webhook queue depth.
"""
import time
import os
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
import psutil

from config.loader import ConfigManager

router = APIRouter()

# In-memory counters (for a full deployment, use prometheus_client library)
_metrics: dict = {
    "requests_total": {},       # (method, path, status) -> count
    "requests_duration_ms": [], # list of durations
    "rate_limit_hits": 0,
    "auth_failures": 0,
    "webhook_delivered": 0,
    "webhook_failed": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "db_queries_total": 0,
    "db_query_errors": 0,
}

_start_time = time.time()

def increment(key: str, labels: dict = None):
    """Increment a named counter, optionally scoped by labels."""
    global _metrics
    if labels:
        label_key = f"{key}|{','.join(f'{k}={v}' for k,v in sorted(labels.items()))}"
        _metrics["requests_total"][label_key] = _metrics["requests_total"].get(label_key, 0) + 1
    else:
        _metrics[key] = _metrics.get(key, 0) + 1

def record_duration(duration_ms: float):
    _metrics["requests_duration_ms"].append(duration_ms)
    # Keep only the last 10000 samples to bound memory
    if len(_metrics["requests_duration_ms"]) > 10000:
        _metrics["requests_duration_ms"] = _metrics["requests_duration_ms"][-5000:]

def _format_metric(name: str, value, labels: dict = None, help_text: str = "", metric_type: str = "counter") -> str:
    lines = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")
    return "\n".join(lines)

@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request, format: str = "prometheus"):
    proc = psutil.Process(os.getpid())
    mem_mb = proc.memory_info().rss / 1024 / 1024
    cpu_pct = proc.cpu_percent()
    uptime = time.time() - _start_time

    durations = _metrics["requests_duration_ms"]
    avg_duration = sum(durations) / len(durations) if durations else 0
    p99 = sorted(durations)[int(len(durations) * 0.99)] if len(durations) > 100 else avg_duration

    # 1. Clean JSON Format for Humans
    if format == "json":
        return {
            "status": "online",
            "uptime_seconds": round(uptime, 2),
            "system": {
                "memory_mb": round(mem_mb, 2),
                "cpu_percent": round(cpu_pct, 2)
            },
            "performance": {
                "avg_request_ms": round(avg_duration, 3),
                "p99_request_ms": round(p99, 3)
            },
            "counters": {
                "rate_limit_hits": _metrics["rate_limit_hits"],
                "auth_failures": _metrics["auth_failures"],
                "cache": {
                    "hits": _metrics["cache_hits"],
                    "misses": _metrics["cache_misses"]
                },
                "database": {
                    "queries": _metrics["db_queries_total"],
                    "errors": _metrics["db_query_errors"]
                },
                "webhooks": {
                    "delivered": _metrics["webhook_delivered"],
                    "failed": _metrics["webhook_failed"]
                }
            }
        }

    # 2. Raw Prometheus Format for Machines
    lines = [
        "# NexusGate Metrics",
        _format_metric("nexusgate_uptime_seconds", round(uptime, 2), help_text="Server uptime", metric_type="gauge"),
        _format_metric("nexusgate_memory_mb", round(mem_mb, 2), help_text="Memory usage", metric_type="gauge"),
        _format_metric("nexusgate_cpu_percent", round(cpu_pct, 2), help_text="CPU usage", metric_type="gauge"),
        _format_metric("nexusgate_request_duration_avg_ms", round(avg_duration, 3), help_text="Avg latency", metric_type="gauge"),
        _format_metric("nexusgate_request_duration_p99_ms", round(p99, 3), help_text="P99 latency", metric_type="gauge"),
        _format_metric("nexusgate_rate_limit_hits_total", _metrics["rate_limit_hits"]),
        _format_metric("nexusgate_auth_failures_total", _metrics["auth_failures"]),
        _format_metric("nexusgate_webhook_delivered_total", _metrics["webhook_delivered"]),
        _format_metric("nexusgate_webhook_failed_total", _metrics["webhook_failed"]),
        _format_metric("nexusgate_cache_hits_total", _metrics["cache_hits"]),
        _format_metric("nexusgate_cache_misses_total", _metrics["cache_misses"]),
        _format_metric("nexusgate_db_queries_total", _metrics["db_queries_total"]),
        _format_metric("nexusgate_db_query_errors_total", _metrics["db_query_errors"]),
    ]

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
