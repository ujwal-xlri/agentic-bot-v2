import logging
import os
from logging.handlers import TimedRotatingFileHandler

LOG_DIR  = os.getenv("LOG_DIR", "/app/logs")
LOG_FILE = os.path.join(LOG_DIR, "activity.log")

_FMT = logging.Formatter(
    "%(asctime)s | %(levelname)-5s | %(name)-10s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    os.makedirs(LOG_DIR, exist_ok=True)

    fh = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    fh.setFormatter(_FMT)
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setFormatter(_FMT)
    ch.setLevel(logging.INFO)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger
