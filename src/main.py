import gc
import os
import sys

import uvicorn

from config.loader import ConfigManager


def _optimize_garbage_collection():
    """Optimizes GC for high-throughput API serving — reduces pause frequency."""
    gc.set_threshold(700, 10, 10)


def _resolve_workers(cfg_workers: int) -> int:
    if os.name == "nt":
        return 1  # Windows: multi-worker breaks socket sharing
    if cfg_workers > 0:
        return cfg_workers
    return 1  # Single worker by default


def _resolve_config_path() -> str:
    """Parses optional CLI arguments targeting a specific TOML configuration."""
    if len(sys.argv) > 1 and sys.argv[1] == "--config":
        return sys.argv[2]
    return "config.toml"


def _acquire_event_loop_strategy() -> str:
    """Safely delegates execution to the ultra-fast C-backed uvloop if on UNIX."""
    try:
        import uvloop

        uvloop.install()
        return "uvloop"
    except ImportError:
        return "auto"


def main():
    """Main process bootloader natively invoking the Uvicorn ASGI server."""
    _optimize_garbage_collection()

    config_path = _resolve_config_path()
    config = ConfigManager.load(config_path)

    loop_strategy = _acquire_event_loop_strategy()
    actual_workers = _resolve_workers(config.server.workers)

    uvicorn.run(
        "server.app:create_app",
        host=config.server.host,
        port=config.server.port,
        workers=actual_workers,
        factory=True,
        log_level=config.logging.level.lower(),
        timeout_keep_alive=config.server.request_timeout,
        http="httptools",
        loop=loop_strategy,
        limit_concurrency=config.server.max_connections,
        access_log=False,
    )


if __name__ == "__main__":
    main()
