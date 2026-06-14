"""Structured logging (v11) — JSON lines, rotation, no secrets.

Production autonomy needs machine-parseable logs (for Splunk/ELK/Grafana
Loki style ingestion), bounded disk usage, and a hard rule that secrets
never reach a log line. `get_logger()` configures the process-wide
'vulcan' logger exactly once: JSON to stdout (container-friendly) plus a
rotating file at data/logs/vulcan.log (5 MB x 3). Level from
VULCAN_LOG_LEVEL. The formatter redacts anything that looks like an
Anthropic API key defensively.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from vulcan.config import DATA_DIR, log_level

_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = _KEY_RE.sub("sk-ant-***REDACTED***", record.getMessage())
        out = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        for k, v in getattr(record, "fields", {}).items():
            out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def get_logger(name: str = "vulcan") -> logging.Logger:
    global _configured
    logger = logging.getLogger("vulcan")
    if not _configured:
        logger.setLevel(getattr(logging, log_level(), logging.INFO))
        fmt = JsonFormatter()
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        try:
            log_dir = Path(os.environ.get("VULCAN_LOG_DIR", "")
                           or (DATA_DIR / "logs"))
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(log_dir / "vulcan.log",
                                     maxBytes=5_000_000, backupCount=3,
                                     encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            pass                  # read-only FS: stdout logging still works
        logger.propagate = False
        _configured = True
    return logger if name == "vulcan" else logger.getChild(
        name.removeprefix("vulcan."))


def log_event(logger: logging.Logger, level: int, msg: str,
              **fields) -> None:
    """Structured log helper: log_event(log, INFO, 'cycle', alerts=3)."""
    logger.log(level, msg, extra={"fields": fields})
