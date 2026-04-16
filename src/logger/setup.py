import structlog
import logging
import sys
import os
import contextvars
import datetime

from config.loader import ConfigManager

# Context var for storing request id across async functions optionally
request_id_ctx = contextvars.ContextVar('request_id', default='-')

# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_log_level(level_name: str) -> int:
    """Safely maps string configuration flags to Python native numerical log levels."""
    log_level_map = {
        "TRACE": logging.DEBUG, 
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    return log_level_map.get(level_name.upper(), logging.INFO)

def _build_log_handlers(config) -> list[logging.Handler]:
    """Generates the underlying structural streams resolving CLI/File targets."""
    handlers = []
    
    # 1. Console Stream
    if config.logging.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
        
    # 2. Daily Local Output Stream
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_file_path = os.path.join(config.logging.directory, f"{config.logging.file_prefix}_{today}.log")
    handlers.append(logging.FileHandler(log_file_path))
    
    return handlers

def _build_formatter(format_type: str):
    """Provides the renderer bridging internal log events to terminal/JSON outputs."""
    if format_type == "json":
        return structlog.processors.JSONRenderer()
    
    return structlog.dev.ConsoleRenderer(colors=True)

# ─────────────────────────────────────────────────────────────────────────────
# Setup Core
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging():
    """Initializes the global python logging and structlog infrastructure."""
    try:
        config = ConfigManager.get()
    except RuntimeError:
        return

    os.makedirs(config.logging.directory, exist_ok=True)
    
    # 1. Apply StdLib Native Handlers
    logging.basicConfig(
        format="%(message)s",
        level=_resolve_log_level(config.logging.level),
        handlers=_build_log_handlers(config)
    )
    
    # 2. Inject Structlog Pipeline
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    structlog.configure(
        processors=shared_processors + [_build_formatter(config.logging.format)],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
