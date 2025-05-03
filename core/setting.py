import os
from typing import List, Dict
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()


class Settings(BaseSettings):
    # 應用程式設定
    APP_TITLE: str = "Grok API 服務器 MVP"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")
    HOST: str = "0.0.0.0"
    PORT: int = int(os.getenv("PORT", "8000"))

    # CORS 設定
    CORS_ORIGINS: List[str] = ["*"]  # 在生產環境中應更嚴格

    # 安全設定
    SERVER_API_KEY: str = os.getenv("SERVER_API_KEY", "")

    # LLM 模型設定
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "grok-2-1212")

    # Grok API 設定
    GROK_API_URL: str = "https://api.x.ai/v1"
    # 讓 Pydantic 知道這個環境變數是合法的
    GROK_API_KEYS: str = os.getenv("GROK_API_KEYS", "")

    # OpenAI API 設定
    OPENAI_API_KEYS: str = os.getenv("OPENAI_API_KEYS", "")
    OPENAI_API_URL: str = "https://api.openai.com"
    OPENAI_DEFAULT_MODEL: str = os.getenv("OPENAI_DEFAULT_MODEL", "gpt-4.1-2025-04-14")

    # 本地 LLM 設定
    LOCAL_LLM_URL: str = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "llama3-70b")
    # 增加這個字段以符合環境變數
    LOCAL_HEALTH_ENDPOINT: str = os.getenv("LOCAL_HEALTH_ENDPOINT", "http://localhost:11434/health")

    # 費率限制設定
    RATE_LIMIT_RPS: int = int(os.getenv("RATE_LIMIT_RPS", "7"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "5"))
    BASE_RETRY_DELAY: int = int(os.getenv("BASE_RETRY_DELAY", "1"))

    # Redis 設定
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_QUEUE_KEY: str = "grok_api_request_queue"
    REDIS_RESPONSE_PREFIX: str = "response:"
    REDIS_RESPONSE_EXPIRY: int = 3600  # 1 小時

    # 費用計算常數
    PROMPT_TOKEN_COST_PER_MILLION: float = float(os.getenv("PROMPT_TOKEN_COST_PER_MILLION", "2.00"))
    COMPLETION_TOKEN_COST_PER_MILLION: float = float(os.getenv("COMPLETION_TOKEN_COST_PER_MILLION", "10.00"))

    # 故障切換設定
    ENABLE_FAILOVER: bool = os.getenv("ENABLE_FAILOVER", "True").lower() in ("true", "1", "t")
    FAILOVER_PROVIDERS: str = os.getenv("FAILOVER_PROVIDERS", "openai")
    FAILOVER_THRESHOLD: int = int(os.getenv("FAILOVER_THRESHOLD", "3"))  # 連續失敗次數閾值
    FAILOVER_RECOVERY_TIME: int = int(os.getenv("FAILOVER_RECOVERY_TIME", "300"))  # 恢復檢查時間（秒）

    # 健康檢查設定
    ENABLE_HEALTH_CHECKER: bool = os.getenv("ENABLE_HEALTH_CHECKER", "True").lower() in ("true", "1", "t")
    HEALTH_CHECK_INTERVAL: int = int(os.getenv("HEALTH_CHECK_INTERVAL", "60"))  # 健康檢查間隔（秒）
    HEALTH_CHECK_ENDPOINTS: Dict[str, str] = {
        "grok": os.getenv("GROK_HEALTH_ENDPOINT", ""),
        "openai": os.getenv("OPENAI_HEALTH_ENDPOINT", ""),
    }

    # 指標服務設定
    ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "True").lower() in ("true", "1", "t")
    METRICS_WINDOW_HOURS: int = int(os.getenv("METRICS_WINDOW_HOURS", "24"))  # 指標保留時間（小時）

    # 使用新的配置方式並允許任意額外欄位
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"  # 允許環境中的額外變數
    )


# 建立設定實例
settings = Settings()

# 手動解析 GROK_API_KEYS
# 將字串轉換為列表
settings.GROK_API_KEYS = [key.strip() for key in settings.GROK_API_KEYS.split(",") if key.strip()]

settings.OPENAI_API_KEYS = [key.strip() for key in settings.OPENAI_API_KEYS.split(",") if key.strip()]

# # 驗證關鍵設定
# if settings.LLM_PROVIDER == "grok" and not settings.GROK_API_KEYS:
#     raise ValueError("未設定 Grok API 金鑰。請在 .env 檔案中設定 GROK_API_KEYS。")

if not settings.SERVER_API_KEY:
    raise ValueError("未設定服務器 API 金鑰。請在 .env 檔案中設定 SERVER_API_KEY。")
