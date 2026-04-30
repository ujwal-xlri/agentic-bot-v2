import logging
import os
from logging.handlers import TimedRotatingFileHandler
import defaults

LOG_DIR          = os.getenv("LOG_DIR",          defaults.LOG_DIR)
LOG_FILE         = os.path.join(LOG_DIR, "activity.log")
ERROR_LOG_FILE   = os.path.join(LOG_DIR, "error.log")
LOG_LEVEL        = os.getenv("LOG_LEVEL",        defaults.LOG_LEVEL).upper()
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", defaults.LOG_BACKUP_COUNT))

_FMT = logging.Formatter(
    "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(name)-10s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    _level = getattr(logging, LOG_LEVEL, logging.DEBUG)
    logger.setLevel(_level)

    os.makedirs(LOG_DIR, exist_ok=True)

    fh = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", interval=1,
        backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    fh.setFormatter(_FMT)
    fh.setLevel(_level)

    ch = logging.StreamHandler()
    ch.setFormatter(_FMT)
    ch.setLevel(logging.INFO)

    efh = logging.FileHandler(ERROR_LOG_FILE, encoding="utf-8")
    efh.setFormatter(_FMT)
    efh.setLevel(logging.ERROR)

    logger.addHandler(fh)
    logger.addHandler(efh)
    logger.addHandler(ch)

    return logger
