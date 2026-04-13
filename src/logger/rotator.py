import os
import datetime
import asyncio
import structlog
import glob
from config.loader import ConfigManager
from utils.size_parser import parse_size

logger = structlog.get_logger()

async def log_rotator_worker():
    """Background task to rotate logs and GC old ones."""
    logger.info("Log rotator started")
    config = ConfigManager.get()
    
    max_size_bytes = parse_size(config.logging.max_file_size)
    max_files = config.logging.max_files
    directory = config.logging.directory
    prefix = config.logging.file_prefix
    
    while True:
        try:
            await asyncio.sleep(60) # Check every minute
            
            # Simple rotation mechanism
            # Real world production apps typically use tools like logrotate or 
            # logging.handlers.RotatingFileHandler natively. 
            # We implemented a background worker purely to meet the custom specs.
            
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            active_log = os.path.join(directory, f"{prefix}_{today}.log")
            
            # 1. Rotate
            if os.path.exists(active_log):
                size = os.path.getsize(active_log)
                if size > max_size_bytes:
                    # Find next suffix _001, _002 etc
                    idx = 1
                    while True:
                        rotated_name = os.path.join(directory, f"{prefix}_{today}_{idx:03d}.log")
                        if not os.path.exists(rotated_name):
                            break
                        idx += 1
                        
                    try:
                        os.rename(active_log, rotated_name)
                        logger.info("Rotated log file", old=active_log, new=rotated_name, size=size)
                        
                        # Softly notify logging to re-open file handles (needs custom signals or restart)
                        # For now we assume logging.FileHandler will just keep writing if we did a soft rename
                        # but proper rotation requires specific logger setups.
                    except Exception as e:
                        logger.error("Failed to rotate log", error=str(e))
                        
            # 2. GC Old Files
            pattern = os.path.join(directory, f"{prefix}_*.log")
            all_logs = glob.glob(pattern)
            
            if len(all_logs) > max_files:
                # Sort by parsed suffix / modification time
                all_logs.sort(key=os.path.getmtime)
                # Delete oldest
                to_delete = all_logs[:-max_files]
                for f in to_delete:
                    try:
                        os.remove(f)
                        logger.debug("Deleted old log file during GC", file=f)
                    except Exception as e:
                        logger.error("Failed to delete log file", file=f, error=str(e))
                        
        except asyncio.CancelledError:
            logger.info("Log rotator shutting down")
            break
        except Exception as e:
            logger.error("Log rotator encountered error", error=str(e))
