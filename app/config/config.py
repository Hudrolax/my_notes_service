from typing import ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevels = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # TimeZone settings
    TZ: ZoneInfo = Field(ZoneInfo("UTC"), description="Временная зона")

    FAKE_FILE_WORKING: bool = Field(False, description='Мы работаем в тестовой среде без записи реальных файлов')

    # Logging settings
    LOG_LEVEL: LogLevels = Field("INFO", description="Уровень логирования")

    main_warehouse_path: str = Field("Пригородная 26", description="Базовый путь хранилища вещей в БД")

    @field_validator("TZ", mode="before")
    @classmethod
    def _parse_tz(cls, v):
        return ZoneInfo(v) if isinstance(v, str) else v


settings = Settings()
