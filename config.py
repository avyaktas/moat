from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str
    test_database_url: str = "postgresql+psycopg://avyaktasharma@localhost:5432/moat_test"
    anthropic_api_key: str = ""

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



settings = Settings()