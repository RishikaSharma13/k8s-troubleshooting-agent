from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    kubeconfig_path: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
