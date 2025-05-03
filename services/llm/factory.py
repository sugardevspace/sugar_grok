from typing import Dict, Any, List

from core.setting import settings
from core.logger import logger
from services.llm.base import LLMService
from services.llm.grok_api import GrokAPIService
from services.llm.openai_api import OpenAIAPIService

# 全域 LLM 服務實例字典
_llm_services = {}


def get_llm_service() -> LLMService:
    """
    工廠方法：根據配置獲取適當的 LLM 服務

    Returns:
        LLMService: LLM 服務實例

    Raises:
        ValueError: 當找不到指定的 LLM 提供者時
    """
    provider = settings.LLM_PROVIDER.lower()

    # 如果服務已經初始化過，則返回快取的實例
    if provider in _llm_services:
        return _llm_services[provider]

    # 根據提供者建立相應的服務
    if provider == "grok":
        logger.info("初始化 Grok LLM 服務")
        service = GrokAPIService()
        _llm_services[provider] = service
        return service

    # 未來可以在這裡添加其他 LLM 提供者的支援
    elif provider == "openai":
        logger.info("初始化 OpenAI LLM 服務")
        # 動態導入，避免不必要的依賴

        service = OpenAIAPIService()
        _llm_services[provider] = service
        return service

    # 如果找不到提供者，拋出例外
    raise ValueError(f"不支援的 LLM 提供者: {provider}")


def get_llm_stats(provider: str = None) -> Dict[str, Any]:
    """
    獲取 LLM 服務的使用統計

    Args:
        provider: 可選的提供者名稱，若為 None 則返回當前提供者統計

    Returns:
        Dict[str, Any]: 使用統計資訊
    """
    try:
        if provider:
            # 如果指定了特定提供者，創建該提供者的服務
            service = create_llm_service(provider)
        else:
            # 否則使用當前預設提供者
            service = get_llm_service()

        return service.get_stats()
    except Exception as e:
        logger.error(f"獲取 LLM 統計時出錯 (提供者: {provider}): {e}")
        return {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cost": 0.0,
            "requests_per_second": 0.0,
            "error_429_count": 0,
            "other_errors_count": 0
        }


def get_all_providers() -> List[str]:
    """
    獲取所有支援的 LLM 提供者

    Returns:
        List[str]: 提供者列表
    """
    return ["grok", "openai", "anthropic", "local"]


def create_llm_service(provider: str) -> LLMService:
    """
    創建指定提供者的 LLM 服務實例

    Args:
        provider: 提供者名稱

    Returns:
        LLMService: LLM 服務實例

    Raises:
        ValueError: 當找不到指定的 LLM 提供者時
    """
    # 暫存當前設定
    current_provider = settings.LLM_PROVIDER

    # 臨時修改設定
    settings.LLM_PROVIDER = provider

    try:
        # 建立服務實例
        service = get_llm_service()
        return service
    finally:
        # 恢復原始設定
        settings.LLM_PROVIDER = current_provider
