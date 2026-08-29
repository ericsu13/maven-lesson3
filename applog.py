"""Shared logger so each agent's output can be inspected.

Writes to both the console and logs/competitive_analysis.log.
"""

import logging
import os
import sys

os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("competitive_analysis")

if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    file_handler = logging.FileHandler("logs/competitive_analysis.log")
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
