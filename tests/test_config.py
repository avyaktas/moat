"""Settings must survive the env file we ship as documentation.

.env.example sets TEST_DATABASE_URL= to show the variable exists.
pydantic-settings reads an empty assignment as the empty string - a value,
which overrides the field default - so a fresh clone got create_engine("")
and "Could not parse SQLAlchemy URL" before a single test ran.
"""

from moat.config import Settings


def _settings(**overrides) -> Settings:
    base = {"database_url": "postgresql+psycopg://u@localhost/db", "_env_file": None}
    base.update(overrides)
    return Settings(**base)


def test_blank_test_database_url_falls_back_to_the_default():
    s = _settings(test_database_url="")
    assert s.test_database_url.startswith("postgresql+psycopg://")


def test_whitespace_only_test_database_url_falls_back():
    s = _settings(test_database_url="   ")
    assert s.test_database_url.startswith("postgresql+psycopg://")


def test_a_real_test_database_url_is_respected():
    s = _settings(test_database_url="postgresql+psycopg://me@host/other")
    assert s.test_database_url == "postgresql+psycopg://me@host/other"


def test_db_url_adds_the_driver_for_managed_providers():
    s = _settings(database_url="postgresql://u:p@host:5432/db")
    assert s.db_url == "postgresql+psycopg://u:p@host:5432/db"


def test_db_url_leaves_an_explicit_driver_alone():
    s = _settings(database_url="postgresql+psycopg://u@host/db")
    assert s.db_url == "postgresql+psycopg://u@host/db"


def test_db_url_rewrites_only_the_prefix():
    """replace(count=1): a database named 'postgresql://' in the path must not
    be rewritten too."""
    s = _settings(database_url="postgresql://u@host/postgresql://weird")
    assert s.db_url == "postgresql+psycopg://u@host/postgresql://weird"


def test_anthropic_key_is_stripped():
    s = _settings(anthropic_api_key="sk-ant-123\n")
    assert s.anthropic_key == "sk-ant-123"


def test_anthropic_key_absent_is_empty_not_none():
    assert _settings().anthropic_key == ""
