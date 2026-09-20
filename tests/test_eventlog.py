import json
import logging

from embodied_jev.eventlog import configure_logging, write_event


def test_runtime_and_api_events_share_rotating_jsonl_without_request_data(tmp_path):
    logger = logging.getLogger("embodied_jev")
    previous = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    try:
        path = tmp_path / "events.jsonl"
        configure_logging(path)
        write_event({"event": "started", "level": "info", "episode_id": "e1"})
        logging.getLogger("embodied_jev.server").info(
            "not persisted: private-request-body", extra={"event": "connection_test_finished",
            "provider": "chat", "latency_ms": 17, "api_key": "secret-key"})
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["event"] for row in rows] == ["started", "connection_test_finished"]
        assert rows[1]["latency_ms"] == 17
        assert "private-request-body" not in path.read_text()
        assert "secret-key" not in path.read_text()
        files = [handler for handler in logger.handlers if hasattr(handler, "maxBytes")]
        assert files[0].maxBytes == 2_000_000 and files[0].backupCount == 3
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.level, logger.propagate = previous
