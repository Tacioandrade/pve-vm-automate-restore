import logging
import os
from logging.handlers import RotatingFileHandler
from pbs_restore.constants import LOG_FILE, LOGS_DIR

def setup_logging(level_name="INFO"):
    level = getattr(logging, level_name.upper(), logging.INFO)
    
    # Ensure logs directory exists
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    
    # File handler
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Remove existing handlers if any (to avoid duplicates if called multiple times)
    while root_logger.handlers:
        root_logger.removeHandler(root_logger.handlers[0])
    
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Silence third-party loggers if they are too noisy
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("proxmoxer").setLevel(logging.WARNING)
    
    logging.info(f"Sistema de logging iniciado (Nível: {level_name})")
