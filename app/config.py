"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Project AI"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_provider: str = "deepseek"  # "deepseek" | "openai" | "anthropic"
    llm_model: str = "deepseek-v4-flash"

    # Zepto
    zepto_url: str = "https://www.zepto.com"
    zepto_phone: str = ""
    zepto_pincode: str = ""
    browser_headless: bool = False

    # Dry-run mode (testing)
    dry_run: bool = False

    # Playwright
    playwright_cookies_path: str = ".zepto_cookies.json"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
