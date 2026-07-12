from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_env: str = 'development'
    app_secret: str = 'development-only-change-me-32-bytes'
    web_base_url: str = 'http://localhost:8080'
    database_url: str = 'sqlite:///./blaze.db'
    bot_token: str = ''
    bot_username: str = ''
    telegram_webhook_secret: str = 'change-me'
    google_client_id: str = ''
    google_client_secret: str = ''
    github_client_id: str = ''
    github_client_secret: str = ''
    smtp_host: str = ''
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    smtp_starttls: bool = True
    magic_link_ttl_seconds: int = Field(900, ge=300, le=3600)
    owner_ids: str = ''
    max_upload_mb: int = Field(10, ge=1, le=50)
    global_workload_limit: int = Field(0, ge=0, le=10000)
    enable_database_provisioning: bool = True
    default_cpu_vcpus: float = Field(0.5, ge=0.1, le=32)
    default_memory_mb: int = Field(512, ge=128, le=131072)
    max_databases_per_workload: int = Field(1, ge=0, le=10)
    session_ttl_seconds: int = Field(86400, ge=300, le=2592000)
    railway_api_token: str = ''
    railway_project_id: str = ''
    railway_environment_id: str = ''
    railway_runner_image: str = 'ghcr.io/blazenxt/vps-blazenxt-runner:latest'
    railway_api_url: str = 'https://backboard.railway.com/graphql/v2'
    runner_token_ttl_seconds: int = Field(2592000, ge=60, le=31536000)
    @field_validator('app_secret')
    @classmethod
    def validate_secret(cls, value):
        if len(value) < 32: raise ValueError('APP_SECRET must be at least 32 characters')
        return value
    @property
    def owners(self): return {int(x) for x in self.owner_ids.split(',') if x.strip().isdigit()}
    @property
    def production(self): return self.app_env.lower() == 'production'

@lru_cache
def get_settings(): return Settings()
