import sys
import uvicorn
from config.loader import ConfigManager

def main():
    # Load config initially before app starts.
    config_path = "config.toml"
    if len(sys.argv) > 1 and sys.argv[1] == "--config":
        config_path = sys.argv[2]

    config = ConfigManager.load(config_path)

    uvicorn.run(
        "server.app:create_app",
        host=config.server.host,
        port=config.server.port,
        workers=config.server.workers if config.server.workers > 0 else 1,
        factory=True,
        log_level=config.logging.level.lower(),
        timeout_keep_alive=config.server.request_timeout,
        http="httptools",
        limit_concurrency=config.server.max_connections
    )

if __name__ == "__main__":
    main()
