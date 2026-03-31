import sys
import gc
import uvicorn
from config.loader import ConfigManager

def main():
    # Keep CPU/RAM usage low by tweaking the GC thresholds and forcing eager cleanup
    # Default is (700, 10, 10). Let's lower generation 0 threshold to free memory frequently.
    gc.set_threshold(300, 5, 5)

    # Load config initially before app starts.
    config_path = "config.toml"
    if len(sys.argv) > 1 and sys.argv[1] == "--config":
        config_path = sys.argv[2]

    config = ConfigManager.load(config_path)

    # Attempt to load uvloop for ultra-fast C-based asyncio event loop (Unix only)
    try:
        import uvloop
        uvloop.install()
        loop_opt = "uvloop"
    except ImportError:
        loop_opt = "auto"

    uvicorn.run(
        "server.app:create_app",
        host=config.server.host,
        port=config.server.port,
        workers=config.server.workers if config.server.workers > 0 else 1,
        factory=True,
        log_level=config.logging.level.lower(),
        timeout_keep_alive=config.server.request_timeout,
        http="httptools",
        loop=loop_opt,
        limit_concurrency=config.server.max_connections
    )

if __name__ == "__main__":
    main()
