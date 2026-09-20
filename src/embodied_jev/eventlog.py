"""Bounded, structured application logs; request bodies and credentials are never logged."""
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("embodied_jev.events")
logger.addHandler(logging.NullHandler())


class EventFormatter(logging.Formatter):
    """Only our known event fields enter the persistent application log."""
    def format(self, record):
        if record.name == "embodied_jev.events":
            return record.getMessage()
        entry = {"time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                 "level": record.levelname.lower(), "event": getattr(record, "event", "application_event")}
        for key in ("episode_id", "previous_episode_id", "comparison_id", "lane_id",
                    "provider", "task", "action", "verification_status", "latency_ms"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def configure_logging(path=None):
    application = logging.getLogger("embodied_jev")
    application.setLevel(logging.INFO)
    application.propagate = False
    for handler in application.handlers[:]:
        handler.close()
        application.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(EventFormatter())
    application.addHandler(handler)
    if path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(output, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(EventFormatter())
        application.addHandler(file_handler)


def write_event(event):
    logger.log({"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}[event["level"]],
               json.dumps(event, ensure_ascii=False, separators=(",", ":")))
