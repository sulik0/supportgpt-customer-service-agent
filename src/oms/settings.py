"""OMS 使用独立数据库和凭据，不复用客服 JWT 或 Tool 加密密钥。"""

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReferenceOMSSettings(BaseSettings):
    """参考服务默认只接受 PostgreSQL，SQLite 仅用于隔离测试。"""

    model_config = SettingsConfigDict(
        env_prefix="REFERENCE_OMS_", env_file=None, hide_input_in_errors=True
    )
    database_url: str = Field(repr=False)
    api_key: SecretStr
    encryption_key: SecretStr
    environment: str = "development"

    @model_validator(mode="after")
    def validate_configuration(self):
        """拒绝缺省密钥及意外启用的非持久化后端。"""
        if len(self.api_key.get_secret_value()) < 32:
            raise ValueError(
                "Reference OMS API key must contain at least 32 characters"
            )
        Fernet(self.encryption_key.get_secret_value().encode())
        if not self.database_url.startswith("postgresql+asyncpg://") and not (
            self.environment == "testing"
            and self.database_url.startswith("sqlite+aiosqlite://")
        ):
            raise ValueError("Reference OMS requires PostgreSQL outside isolated tests")
        return self
