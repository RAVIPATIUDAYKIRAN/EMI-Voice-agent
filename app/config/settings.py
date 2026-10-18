import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Centralized application configuration loaded from environment or .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # MySQL Database Configuration
    mysql_user: str | None = Field(
        default=None,
        validation_alias="MYSQL_USER",
        description="MySQL database user",
    )
    mysql_password: SecretStr | None = Field(
        default=None,
        validation_alias="MYSQL_PASSWORD",
        description="MySQL database password",
    )
    mysql_host: str = Field(
        default="127.0.0.1",
        validation_alias="MYSQL_HOST",
        description="MySQL host address",
    )
    mysql_port: int = Field(
        default=3306,
        validation_alias="MYSQL_PORT",
        description="MySQL port number",
    )
    mysql_database: str = Field(
        default="emi_voice_agent",
        validation_alias="MYSQL_DATABASE",
        description="MySQL database name",
    )

    # OpenAI / LLM Configuration
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API key (masked)",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias="OPENAI_MODEL",
        description="OpenAI model identifier",
    )

    @field_validator("mysql_user")
    @classmethod
    def validate_mysql_user(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned or None
        return None

    @field_validator("mysql_password")
    @classmethod
    def validate_mysql_password(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and not v.get_secret_value():
            return None
        return v

    @field_validator("openai_model")
    @classmethod
    def validate_openai_model(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("OPENAI_MODEL environment variable cannot be empty.")
        return cleaned

    @property
    def has_database_credentials(self) -> bool:
        """Check whether mandatory database credentials are fully supplied."""
        return bool(
            self.mysql_user
            and self.mysql_password
            and self.mysql_password.get_secret_value()
        )

    @property
    def async_database_url(self) -> URL:
        """Build the asynchronous SQLAlchemy database URL.

        Raises RuntimeError if database credentials are not configured.
        """
        if not self.has_database_credentials:
            raise RuntimeError(
                "Database credentials are not configured (MYSQL_USER and MYSQL_PASSWORD are required)"
            )
        return URL.create(
            drivername="mysql+asyncmy",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()


settings = get_settings()
