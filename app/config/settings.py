
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


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

    # ---------------------------------------------------------
    # MySQL Database Configuration
    # ---------------------------------------------------------
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
    )
    mysql_port: int = Field(
        default=3306,
        validation_alias="MYSQL_PORT",
        gt=0,
        le=65535,
    )
    mysql_database: str = Field(
        default="emi_voice_agent",
        validation_alias="MYSQL_DATABASE",
    )

    # ---------------------------------------------------------
    # OpenAI Configuration: LLM, STT and TTS
    # ---------------------------------------------------------
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API key",
    )
    openai_model: str = Field(
        default="gpt-6-luna",
        validation_alias="OPENAI_MODEL",
        description="LLM model identifier",
    )
    openai_stt_model: str = Field(
        default="whisper-1",
        validation_alias="OPENAI_STT_MODEL",
        description="Speech-to-text model",
    )
    openai_tts_model: str = Field(
        default="gpt-4o-mini-tts",
        validation_alias="OPENAI_TTS_MODEL",
        description="Text-to-speech model",
    )
    openai_tts_voice: str = Field(
        default="coral",
        validation_alias="OPENAI_TTS_VOICE",
        description="Text-to-speech voice",
    )

    # Bounded audio inputs/outputs shared by the WAV route and telephony bridge.
    local_audio_max_bytes: int = Field(
        default=10_000_000, validation_alias="LOCAL_AUDIO_MAX_BYTES", gt=0, le=25_000_000
    )
    local_audio_max_seconds: int = Field(
        default=120, validation_alias="LOCAL_AUDIO_MAX_SECONDS", gt=0, le=600
    )

    # ---------------------------------------------------------
    # Twilio Outbound Voice Configuration
    # ---------------------------------------------------------
    twilio_account_sid: str | None = Field(
        default=None,
        validation_alias="TWILIO_ACCOUNT_SID",
    )
    twilio_auth_token: SecretStr | None = Field(
        default=None,
        validation_alias="TWILIO_AUTH_TOKEN",
    )
    twilio_from_number: str | None = Field(
        default=None,
        validation_alias="TWILIO_FROM_NUMBER",
    )
    twilio_allowed_destinations: str = Field(
        default="",
        validation_alias="TWILIO_ALLOWED_DESTINATIONS",
        description="Comma-separated E.164 allowlist for outbound voice calls",
    )
    public_base_url: str | None = Field(
        default=None,
        validation_alias="PUBLIC_BASE_URL",
    )

    # ---------------------------------------------------------
    # Dashboard and Callback Configuration
    # ---------------------------------------------------------
    dashboard_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DASHBOARD_API_KEY",
        description="Secret for protected dashboard/callback operations",
    )
    callback_worker_enabled: bool = Field(
        default=False,
        validation_alias="CALLBACK_WORKER_ENABLED",
    )
    voice_auto_retry_enabled: bool = Field(
        default=False,
        validation_alias="VOICE_AUTO_RETRY_ENABLED",
    )

    # ---------------------------------------------------------
    # Validation
    # ---------------------------------------------------------
    @field_validator("mysql_user")
    @classmethod
    def validate_mysql_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("mysql_password")
    @classmethod
    def validate_mysql_password(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None
        return value

    @field_validator(
        "openai_model",
        "openai_stt_model",
        "openai_tts_model",
        "openai_tts_voice",
    )
    @classmethod
    def validate_model_settings(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("OpenAI model and voice settings cannot be empty.")
        return cleaned

    # ---------------------------------------------------------
    # Credential Checks
    # ---------------------------------------------------------
    @property
    def has_openai_credentials(self) -> bool:
        return bool(self.openai_api_key.get_secret_value().strip())

    @property
    def has_twilio_credentials(self) -> bool:
        return bool(
            self.twilio_account_sid
            and self.twilio_account_sid.strip()
            and self.twilio_auth_token
            and self.twilio_auth_token.get_secret_value().strip()
            and self.twilio_from_number
            and self.twilio_from_number.strip()
        )

    @property
    def authorized_twilio_destinations(self) -> frozenset[str]:
        return frozenset(
            item.strip()
            for item in self.twilio_allowed_destinations.split(",")
            if item.strip()
        )

    @property
    def has_database_credentials(self) -> bool:
        return bool(
            self.mysql_user
            and self.mysql_password
            and self.mysql_password.get_secret_value()
        )

    @property
    def has_openai_voice_config(self) -> bool:
        """Check credentials and configured STT/TTS identifiers without network access."""
        return self.has_openai_credentials and bool(
            self.openai_stt_model and self.openai_tts_model and self.openai_tts_voice
        )

    # ---------------------------------------------------------
    # Async Database URL
    # ---------------------------------------------------------
    @property
    def async_database_url(self) -> URL:
        """Build the async SQLAlchemy URL without exposing the password."""

        if not self.has_database_credentials:
            raise RuntimeError(
                "Database credentials are not configured. "
                "MYSQL_USER and MYSQL_PASSWORD are required."
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
    """Return the cached application settings singleton."""
    return Settings()


settings = get_settings()
