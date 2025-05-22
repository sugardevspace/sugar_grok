import asyncio
import time
from typing import Dict, Any, List, Optional

from fastapi import HTTPException
from core.setting import settings
from core.logger import logger
from services.llm.base import LLMService
from utils.api_key_manager import get_key_manager
from utils.cost_calculator import calculate_cost
from models.structure_response_model import (StoryMessageResponse, ChatMessageResponse, StimulationResponse,
                                             IntimacyResponse, UserPersona, healthCheckResponse, LevelMessageResponse)

# 引入 OpenAI 客戶端
from openai import OpenAI


class GrokAPIService(LLMService):
    """Grok API 服務實作"""

    def __init__(self):
        """初始化 Grok API 服務"""
        self.api_url = settings.GROK_API_URL  # https://api.x.ai/v1
        self.key_manager = get_key_manager()
        self.max_retries = settings.MAX_RETRIES
        self.base_retry_delay = settings.BASE_RETRY_DELAY
        self._default_model = settings.DEFAULT_MODEL

        # 追蹤指標
        self.request_timestamps: List[float] = []
        self.total_tokens = {"prompt": 0, "completion": 0}
        self.total_cost = 0.0
        self.error_counts = {"429": 0, "other": 0}

        self.RESPONSE_MODEL = {
            "story": StoryMessageResponse,
            "text": ChatMessageResponse,
            "stimulation": StimulationResponse,
            "level": LevelMessageResponse,
            "intimacy": IntimacyResponse,
            "health_check": healthCheckResponse,
            "user_persona": UserPersona,
        }

    async def call_api(self, request_data: Dict[str, Any], request_id: Optional[str] = None) -> Dict[str, Any]:
        """
        呼叫 Grok API 的核心方法，支援結構化輸出與錯誤處理
        Args:
            request_data: 請求數據，包含模型、訊息等參數
            request_id: 可選的請求 ID

        Returns:
            Dict[str, Any]: API 回應數據
        Raises:
            HTTPException: 當 API 呼叫失敗時，拋出錯誤以觸發故障切換機制
        """
        # 複製請求數據，避免修改原始數據
        request_data = request_data.copy() if request_data else {}

        # 提取必要參數
        model = request_data.get('model', self.default_model)
        messages = request_data.get('messages', [])

        # 檢查是否要求結構化輸出
        response_format = request_data.get("response_format")
        pydantic_model = None

        # 檢查 response_format 是否為字串索引
        if response_format and isinstance(response_format, str) and response_format in self.RESPONSE_MODEL:
            pydantic_model = self.RESPONSE_MODEL[response_format]
            logger.info(f"檢測到字串索引 '{response_format}' 的結構化輸出請求")
        else:
            logger.info("未指定結構化輸出類型，使用預設結構化輸出")
            pydantic_model = self.RESPONSE_MODEL["story"]

        # 如果需要結構化輸出，確保使用相容模型
        if model not in await self.get_model_list():
            original_model = model
            model = await self._get_best_model_for_structured_output()
            request_data['model'] = model
            logger.info(f"結構化輸出需要相容模型，將 {original_model} 轉換為 {model}")

        # 準備請求參數
        kwargs = {"model": model, "messages": messages, "response_format": pydantic_model}

        # 添加其他參數
        for param in ["temperature", "max_tokens", "top_p", "stream"]:
            if param in request_data:
                kwargs[param] = request_data[param]

        logger.info(f"使用模型: {model}, API 路徑: {self.api_url}")

        # 輸出 retry 的次數
        rate_limit_retry_count = 0

        # 嘗試使用可用的金鑰
        tried_keys = set()
        available_keys = set(self.key_manager.provider_keys.get("grok", []))

        # 只在 API 金鑰錯誤或速率限制錯誤時重試
        while available_keys and (not tried_keys or tried_keys != available_keys):
            try:
                # 獲取 API 金鑰
                api_key = await self.key_manager.get_next_key("grok")

                # 如果已嘗試過此金鑰則跳過（除非是速率限制重試）
                if api_key in tried_keys:
                    continue

                tried_keys.add(api_key)

                # 初始化 OpenAI 客戶端
                client = OpenAI(api_key=api_key, base_url=self.api_url)

                # 使用 beta.chat.completions.parse 方法
                logger.info(f"使用 beta.chat.completions.parse 方法處理 '{response_format}' 結構化輸出")

                completion = await asyncio.wait_for(asyncio.to_thread(client.beta.chat.completions.parse, **kwargs),
                                                    timeout=30)

                # 取得解析後的 Pydantic 對象
                parsed_obj = completion.choices[0].message.parsed
                logger.info(f"成功解析 '{response_format}' 結構化模型")
                logger.info(f"解析後的模型: {parsed_obj.model_dump()}")

                # 構建回應
                response_data = {
                    "id": completion.id,
                    "object": "chat.completion",
                    "created": int(time.time()),  # openai 的格式
                    "model": model,
                    "finish_reason": completion.choices[0].finish_reason,
                    "structured_output": parsed_obj.model_dump(),
                    "response_format_type": response_format,
                    "status": "completed",
                }

                # 添加使用量統計
                if hasattr(completion, "usage") and completion.usage:
                    response_data["usage"] = {
                        "prompt_tokens": completion.usage.prompt_tokens,
                        "completion_tokens": completion.usage.completion_tokens,
                        "total_tokens": completion.usage.total_tokens
                    }

                    # 更新內部統計
                    self.total_tokens["prompt"] += completion.usage.prompt_tokens
                    self.total_tokens["completion"] += completion.usage.completion_tokens

                    cost = calculate_cost(completion.usage.prompt_tokens, completion.usage.completion_tokens, "grok")
                    self.total_cost += cost

                    logger.info(f"請求消耗: {completion.usage.prompt_tokens} 提示 tokens, "
                                f"{completion.usage.completion_tokens} 完成 tokens, 費用: ${cost:.6f}")

                # 記錄請求時間戳
                self.request_timestamps.append(time.time())

                return response_data

            except Exception as e:
                error_str = str(e)
                error_type = type(e).__name__

                # 處理金鑰錯誤：標記無效並嘗試下一個金鑰
                if "authentication" in error_str.lower() or "api key" in error_str.lower():
                    logger.warning(f"API 金鑰錯誤: {error_str}")
                    await self.key_manager.mark_key_invalid("grok", api_key)
                    logger.warning(f"已標記無效的 Grok API 金鑰，嘗試使用其他金鑰 ({len(tried_keys)}/{len(available_keys)})")
                    continue  # 繼續嘗試下一個金鑰

                # 處理速率限制錯誤：使用 Exponential Backoff Retry
                elif "rate limit" in error_str.lower() or "429" in error_str:
                    self.error_counts["429"] += 1
                    rate_limit_retry_count += 1

                    if rate_limit_retry_count <= self.max_retries:
                        retry_wait = self.base_retry_delay * (2**(rate_limit_retry_count - 1))  # 指數退避
                        retry_wait = min(retry_wait, 30)  # 最大等待30秒

                        logger.warning(
                            f"達到速率限制，等待 {retry_wait} 秒後使用相同的金鑰重試 (重試 {rate_limit_retry_count}/{self.max_retries})")
                        await asyncio.sleep(retry_wait)

                        # 重試時使用相同的金鑰，所以從 tried_keys 中移除
                        if api_key in tried_keys:
                            tried_keys.remove(api_key)
                        continue
                    else:
                        logger.error(f"達到速率限制最大重試次數 ({self.max_retries})，嘗試使用其他金鑰")
                        # 不再使用這個金鑰，繼續嘗試下一個

                # 處理方法或屬性不存在的錯誤（如缺少 beta.chat.completions.parse）
                elif "has no attribute" in error_str:
                    logger.error(f"API 不支援所需方法: {error_str}")
                    # 這類錯誤無法通過重試解決，直接拋出
                    raise

                # 對於其他錯誤，直接拋出以觸發故障切換
                else:
                    logger.error(f"Grok API 呼叫失敗 ({error_type}): {error_str}")
                    raise

        # 如果所有金鑰都嘗試過但仍失敗
        if tried_keys and tried_keys == available_keys:
            logger.error("所有 Grok API 金鑰均無效或達到速率限制")
            raise HTTPException(status_code=401, detail="所有 Grok API 金鑰均無效或達到速率限制")

    async def _get_best_model_for_structured_output(self) -> str:
        """
        獲取最佳的結構化輸出模型

        Returns:
            str: 最適合用於結構化輸出的模型名稱
        """
        available_models = await self.get_model_list()

        # 優先順序：mini-fast > mini > fast > 標準
        if "grok-3-mini-fast" in available_models:
            return "grok-3-mini-fast"
        elif "grok-3-mini" in available_models:
            return "grok-3-mini"
        elif "grok-3-fast" in available_models:
            return "grok-3-fast"
        elif "grok-3" in available_models:
            return "grok-3"
        else:
            # 回退到可用的第一個模型
            return available_models[0] if available_models else self.default_model

    def get_stats(self) -> Dict[str, Any]:
        """
        獲取 API 使用統計

        Returns:
            Dict[str, Any]: 使用統計資訊
        """
        # 計算每秒請求數
        now = time.time()
        recent_requests = len([ts for ts in self.request_timestamps if now - ts <= 60])
        rps = recent_requests / 60 if recent_requests > 0 else 0

        return {
            "total_requests": len(self.request_timestamps),
            "total_prompt_tokens": self.total_tokens["prompt"],
            "total_completion_tokens": self.total_tokens["completion"],
            "total_cost": self.total_cost,
            "requests_per_second": rps,
            "error_429_count": self.error_counts["429"],
            "other_errors_count": self.error_counts["other"]
        }

    @property
    def default_model(self) -> str:
        """取得預設模型名稱"""
        return self._default_model

    @property
    def provider_name(self) -> str:
        """取得提供者名稱"""
        return "grok"

    async def get_model_list(self) -> List[str]:
        """
        獲取 Grok 支援的模型列表

        Returns:
            List[str]: 模型列表
        """
        # Grok 目前支援的模型列表，加入結構化輸出支援的模型
        return ["grok-3-mini-fast", "grok-3-mini", "grok-3-fast", "grok-3"]
