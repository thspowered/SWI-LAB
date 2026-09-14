from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfiguracia citana z prostredia alebo zo suboru .env."""

    database_url: str = "postgresql+psycopg://swilab:swilab@localhost:5433/swilab"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
