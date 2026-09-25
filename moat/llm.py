"""One shared Anthropic client.

analysis.answer_question and report.synthesize each built a client per call:

    client = client or Anthropic(api_key=settings.anthropic_key)

Every Anthropic() constructs its own httpx client, which means its own
connection pool and its own TLS handshake on first use. Per request that is a
fresh TCP connection and a full handshake to api.anthropic.com before any
work begins - pure latency, paid on exactly the endpoints that are already
the slowest, and repeated for the report path which calls the API twice.

The client is built lazily rather than at import so that importing analysis
or report does not require an API key. Tests, the ingest CLI and the offline
grounding replay all import these modules without one, and constructing the
client eagerly would make an unrelated import fail on a missing credential.

Double-checked locking because FastAPI runs sync endpoints in a threadpool,
so two requests can genuinely race here on a cold process. Losing that race
is harmless - you would build one extra client - but the lock keeps the
invariant simple to state.
"""

import threading

_client = None
_lock = threading.Lock()


def get_client():
    """Return the process-wide Anthropic client, building it on first use."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                from anthropic import Anthropic

                from moat.config import settings

                _client = Anthropic(api_key=settings.anthropic_key)
    return _client


def reset_client() -> None:
    """Drop the cached client. Used by tests."""
    global _client
    with _lock:
        _client = None
