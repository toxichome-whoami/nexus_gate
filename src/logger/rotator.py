import os
import datetime
import asyncio
import structlog
import glob
from config.loader import ConfigManager
from utils.size_parser import parse_size

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Internal Subsystems
# ─────────────────────────────────────────────────────────────────────────────

def _get_next_rotated_path(directory: str, prefix: str, today: str) -> str:
    """Scans existing rolled logs to determine the next sequential suffix."""
    idx = 1
    while True:
        rotated_name = os.path.join(directory, f"{prefix}_{today}_{idx:03d}.log")
        if not os.path.exists(rotated_name):
            return rotated_name
        idx += 1

def _rotate_active_log(active_log: str, max_size_bytes: int, directory: str, prefix: str, today: str):
    """Checks the live log file size and moves it to a sequential suffix if over limit."""
    if not os.path.exists(active_log):
        return

    size = os.path.getsize(active_log)
    if size <= max_size_bytes:
        return

    rotated_name = _get_next_rotated_path(directory, prefix, today)
        
    try:
        os.rename(active_log, rotated_name)
        logger.info("Rotated log file", old=active_log, new=rotated_name, size=size)
    except Exception as rotation_error:
        logger.error("Failed to rotate log", error=str(rotation_error))

def _garbage_collect_logs(directory: str, prefix: str, max_files: int):
    """Enforces the file retention policy by purging the oldest logs physically."""
    pattern = os.path.join(directory, f"{prefix}_*.log")
    all_logs = glob.glob(pattern)
    
    if len(all_logs) <= max_files:
        return

    # Sort strictly by modification time ascending (oldest first)
    all_logs.sort(key=os.path.getmtime)
    
    old_files_to_delete = all_logs[:-max_files]
    for target_file in old_files_to_delete:
        try:
            os.remove(target_file)
            logger.debug("Deleted old log file during GC", file=target_file)
        except Exception as delete_error:
            logger.error("Failed to delete log file", file=target_file, error=str(delete_error))

# ─────────────────────────────────────────────────────────────────────────────
# Core Daemon
# ─────────────────────────────────────────────────────────────────────────────

async def log_rotator_worker():
    """Background daemon invoking custom log rotations sequentially."""
    logger.info("Log rotator started")
    config = ConfigManager.get()
    
    max_size_bytes = parse_size(config.logging.max_file_size)
    max_files = config.logging.max_files
    directory = config.logging.directory
    prefix = config.logging.file_prefix
    
    while True:
        try:
            await asyncio.sleep(60) # Interval scan
            
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            active_log = os.path.join(directory, f"{prefix}_{today}.log")
            
            _rotate_active_log(active_log, max_size_bytes, directory, prefix, today)
            _garbage_collect_logs(directory, prefix, max_files)
                        
        except asyncio.CancelledError:
            logger.info("Log rotator shutting down")
            break
        except Exception as iteration_error:
            logger.error("Log rotator encountered error", error=str(iteration_error))
