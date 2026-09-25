"""Tests for the shared Anthropic client."""

from moat import analysis, llm, report


def setup_function():
    llm.reset_client()


def test_client_is_built_once(monkeypatch):
    built = []

    class _Fake:
        def __init__(self, api_key=None):
            built.append(api_key)

    monkeypatch.setattr("anthropic.Anthropic", _Fake)
    a = llm.get_client()
    b = llm.get_client()
    assert a is b
    assert len(built) == 1, f"built the client {len(built)} times"


def test_client_is_lazy(monkeypatch):
    """Importing analysis or report must not require an API key.

    The ingest CLI, the test suite and the offline grounding replay all import
    these modules without a credential configured.
    """

    def _explode(**kwargs):
        raise AssertionError("client built at import time")

    monkeypatch.setattr("anthropic.Anthropic", _explode)
    llm.reset_client()
    # Merely touching the modules must not construct anything.
    assert analysis.answer_question is not None
    assert report.synthesize is not None


def test_injected_client_still_wins():
    """The client= parameter is what the tests and evaluate.py depend on."""

    class _Stub:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            class _B:
                type = "text"
                text = '{"addressed": true, "answer": "x", "quotes": []}'

            class _R:
                content = [_B()]

            return _R()

    result = analysis.answer_question("q", "source text", client=_Stub())
    assert result["answer"] == "x"


def test_reset_builds_a_new_client(monkeypatch):
    built = []

    class _Fake:
        def __init__(self, api_key=None):
            built.append(api_key)

    monkeypatch.setattr("anthropic.Anthropic", _Fake)
    llm.get_client()
    llm.reset_client()
    llm.get_client()
    assert len(built) == 2
