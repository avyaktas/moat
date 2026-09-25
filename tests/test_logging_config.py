"""The logging setup has to actually deliver a record.

Before this existed, the root logger had no handlers and an effective level of
WARNING. Every logger.info() in the codebase was discarded silently, and
warnings escaped through logging's last-resort handler as bare text with no
timestamp, level or logger name. A test that only checked "configure_logging
ran without raising" would not have caught any of that, so these assert on
records actually arriving, formatted.
"""

import io
import logging

from moat import logging_config


def _capture(level="INFO"):
    """Configure logging, then redirect its handler at a buffer."""
    logging_config.configure_logging(level)
    buffer = io.StringIO()
    for handler in logging.getLogger().handlers:
        handler.stream = buffer
    return buffer


def test_info_records_are_emitted():
    buffer = _capture("INFO")
    logging.getLogger("moat.test").info("hello from info")
    assert "hello from info" in buffer.getvalue()


def test_records_carry_level_and_logger_name():
    """Bare text is not enough to diagnose anything from."""
    buffer = _capture("INFO")
    logging.getLogger("moat.named").warning("something to look at")
    out = buffer.getvalue()
    assert "WARNING" in out
    assert "moat.named" in out


def test_records_carry_a_timestamp():
    buffer = _capture("INFO")
    logging.getLogger("moat.test").info("stamped")
    # ISO-ish date at the start of the line: 2026-09-24T15:05:52
    assert buffer.getvalue()[:4].isdigit()


def test_level_is_respected():
    buffer = _capture("WARNING")
    logging.getLogger("moat.test").info("should not appear")
    logging.getLogger("moat.test").warning("should appear")
    out = buffer.getvalue()
    assert "should not appear" not in out
    assert "should appear" in out


def test_uvicorn_loggers_share_the_handler():
    """Otherwise the access log and the application log look like two
    different services interleaved."""
    buffer = _capture("INFO")
    logging.getLogger("uvicorn.error").info("from uvicorn")
    assert "from uvicorn" in buffer.getvalue()


def test_configuring_twice_does_not_duplicate_records():
    """Import order should not decide whether every line is logged twice."""
    logging_config.configure_logging("INFO")
    buffer = _capture("INFO")
    logging.getLogger("moat.test").info("once")
    assert buffer.getvalue().count("once") == 1


def test_noisy_third_party_loggers_are_quietened():
    buffer = _capture("INFO")
    logging.getLogger("httpx").info("chatty request detail")
    assert "chatty request detail" not in buffer.getvalue()
