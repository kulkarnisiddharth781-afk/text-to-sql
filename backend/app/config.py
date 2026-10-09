from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Provider SDKs read API keys and base URLs from os.environ, not from Settings.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"  # openai | ollama | anthropic
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_reasoning_effort: str = ""  # low | medium | high for reasoning models such as gpt-oss; empty to omit
    database_path: str = "data/sample.db"
    max_rows: int = 200
    query_timeout_s: float = 5.0
    max_retries: int = 2
    max_history_turns: int = 6
    cors_origins: str = "http://localhost:5173"


settings = Settings()
