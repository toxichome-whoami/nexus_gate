import structlog
import logging
import sys
import os
import contextvars

from config.loader import ConfigManager

# Context var for storing request id across async functions optionally
request_id_ctx = contextvars.ContextVar('request_id', default='-')

def setup_logging():
    try:
        config = ConfigManager.get()
    except RuntimeError:
        # Fallback if config isn't loaded yet
        return
        
    log_level_map = {
        "TRACE": logging.DEBUG,  # Python doesn't have TRACE natively, map to DEBUG
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    
    level = log_level_map.get(config.logging.level.upper(), logging.INFO)
    
    # Processors applied to all log lines
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    # Ensure logs directory exists
    os.makedirs(config.logging.directory, exist_ok=True)
    
    # File handler for active log
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(config.logging.directory, f"{config.logging.file_prefix}_{today}.log")
    
    # Configure stdlib logging
    handlers = []
    
    if config.logging.stdout:
        console_handler = logging.StreamHandler(sys.stdout)
        handlers.append(console_handler)
        
    file_handler = logging.FileHandler(log_file)
    handlers.append(file_handler)
    
    logging.basicConfig(
        format="%(message)s",
        level=level,
        handlers=handlers
    )
    
    # Formatter selection
    if config.logging.format == "json":
        formatter = structlog.processors.JSONRenderer()
    else:
        # Pretty console formatter
        formatter = structlog.dev.ConsoleRenderer(colors=True)
        
    structlog.configure(
        processors=shared_processors + [formatter],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
