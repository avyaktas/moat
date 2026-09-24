from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str
    test_database_url: str = "postgresql+psycopg://avyaktasharma@localhost:5432/moat_test"
    anthropic_api_key: str = ""

    # Rate limiting for the two endpoints that spend money. Burst is how many
    # requests one client may make back to back; per_minute is the sustained
    # rate it refills at. A burst of 0 disables limiting, which is the local
    # default in tests.
    rate_limit_burst: int = 10
    rate_limit_per_minute: float = 20.0

    # Shared secret for ?refresh=, which bypasses the report cache and forces
    # a paid regeneration. Empty means unprotected: convenient locally, and
    # warned about at startup when an API key is present, since that
    # combination is what a real deployment looks like.
    refresh_token: str = ""

    @field_validator("test_database_url", mode="before")
    @classmethod
    def _blank_means_default(cls, v):
        """Treat an empty value as absent, so the field default applies.

        .env.example ships `TEST_DATABASE_URL=` to document the variable's
        existence. pydantic-settings reads that as the empty string - a value,
        which overrides the default - so a fresh clone got
        create_engine("") and an unreadable "Could not parse SQLAlchemy URL"
        before a single test ran.

        Fixing it here rather than in .env.example is what makes it stay
        fixed: the env file is documentation and anyone may copy, edit or
        truncate it, while this holds regardless of what it says. A variable
        set to nothing means "I did not set this", which is what the default
        is for.
        """
        if v is None or (isinstance(v, str) and not v.strip()):
            return "postgresql+psycopg://avyaktasharma@localhost:5432/moat_test"
        return v

    @property
    def db_url(self) -> str:
        """Normalize the driver prefix.

        Managed Postgres providers hand out postgresql:// URLs, but
        SQLAlchemy needs the driver named explicitly. Correcting it here
        means the app works with any provider's format unchanged.
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def anthropic_key(self) -> str:
        """Strip whitespace from the API key.

        Keys pasted into deployment dashboards often arrive with a trailing
        newline, which HTTP headers cannot contain - the request fails with
        an opaque protocol error rather than an auth error. Stripping here
        makes the app tolerant of how the value was entered.
        """
        return self.anthropic_api_key.strip()


settings = Settings()
