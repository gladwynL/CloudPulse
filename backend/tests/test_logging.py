import json
import logging

from app.core.logging import JsonFormatter


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="cloudpulse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_core_fields() -> None:
    output = JsonFormatter(service="cloudpulse-api", environment="test").format(make_record())
    payload = json.loads(output)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "cloudpulse.test"
    assert payload["message"] == "hello world"
    assert payload["service"] == "cloudpulse-api"
    assert payload["environment"] == "test"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_includes_extra_fields() -> None:
    record = make_record(request_id="abc", status_code=200, color_message="\x1b[36mhi\x1b[0m")
    payload = json.loads(JsonFormatter("svc", "test").format(record))
    assert payload["request_id"] == "abc"
    assert payload["status_code"] == 200
    assert "args" not in payload
    assert "color_message" not in payload
