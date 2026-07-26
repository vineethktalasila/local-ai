import sys
import os
import logging
from logging.handlers import RotatingFileHandler

# Ensure a log file path was provided
if len(sys.argv) < 2:
    print("Usage: command 2>&1 | python3 logger.py <logfile>")
    sys.exit(1)

log_file = sys.argv[1]
os.makedirs(os.path.dirname(log_file), exist_ok=True)

# Set Max Size to 50 MB, keeping only 1 previous backup
handler = RotatingFileHandler(log_file, maxBytes=50 * 1024 * 1024, backupCount=1)
handler.setFormatter(logging.Formatter('%(message)s'))

logger = logging.getLogger(log_file)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Read the active stream from the terminal pipe and save it
for line in sys.stdin:
    logger.info(line.rstrip('\n'))
