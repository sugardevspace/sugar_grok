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


class OpenAIAPIService(LLMService):
    """OpenAI API 服務實作"""

    def __init__(self):
        """初始化 OpenAI API 服務"""
        self.api_url = settings.OPENAI_API_URL
        self.key_manager = get_key_manager()
        self.max_retries = settings.MAX_RETRIES
        self.base_retry_delay = settings.BASE_RETRY_DELAY
        self._default_model = settings.OPENAI_DEFAULT_MODEL

        # 追蹤指標
        self.request_timestamps: List[float] = []
        self.total_tokens = {"prompt": 0, "completion": 0}
        self.total_cost = 0.0
        self.error_counts = {"429": 0, "other": 0}

        # 定義結構化輸出模型對照表
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
        呼叫 OpenAI API 的核心方法，支援結構化輸出與錯誤處理
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

        # # 確保模型是 OpenAI 支援的
        # if not model.startswith("gpt-"):
        #     original_model = model
        #     model = self.default_model
        #     request_data['model'] = model
        #     logger.info(f"將非 OpenAI 模型 {original_model} 轉換為 {model}")

        # 檢查是否要求結構化輸出
        response_format = request_data.get("response_format")
        pydantic_model = None
        is_structured_output = False

        # 檢查 response_format 是否為字串索引
        if response_format and isinstance(response_format, str) and response_format in self.RESPONSE_MODEL:
            pydantic_model = self.RESPONSE_MODEL[response_format]
            is_structured_output = True
            logger.info(f"檢測到字串索引 '{response_format}' 的結構化輸出請求")
        elif response_format and isinstance(response_format, dict) and response_format.get("type") == "json_object":
            # 原生 OpenAI JSON 模式
            logger.info("使用 OpenAI 原生 JSON 輸出模式")
        else:
            logger.info("使用標準輸出模式")

        # 輸出 retry 的次數
        rate_limit_retry_count = 0

        # 嘗試使用可用的金鑰
        tried_keys = set()
        available_keys = set(self.key_manager.provider_keys.get("openai", []))

        # 只在 API 金鑰錯誤或速率限制錯誤時重試
        while available_keys and (not tried_keys or tried_keys != available_keys):
            try:
                # 獲取 API 金鑰
                if model == "grok-3":
                    api_key = await self.key_manager.get_next_key("grok")
                else:
                    api_key = await self.key_manager.get_next_key("openai")

                # 如果已嘗試過此金鑰則跳過（除非是速率限制重試）
                if api_key in tried_keys:
                    continue

                tried_keys.add(api_key)

                # 初始化 OpenAI 客戶端
                if model == "grok-3":
                    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")  # Grok API URL
                else:
                    client = OpenAI(api_key=api_key)  # 默認使用 OpenAI API URL

                # 處理結構化輸出
                if is_structured_output and pydantic_model:
                    # 檢查模型是否支援結構化輸出
                    if model not in await self._get_models_with_structured_output():
                        original_model = model
                        model = await self._get_best_model_for_structured_output()
                        logger.info(f"結構化輸出需要相容模型，將 {original_model} 轉換為 {model}")

                    logger.info(f"使用 beta.chat.completions.parse 方法處理 '{response_format}' 結構化輸出")

                    # 準備請求參數
                    parse_kwargs = {"model": model, "messages": messages, "response_format": pydantic_model}
                    for param in ["temperature", "max_tokens", "top_p", "stream"]:
                        if param in request_data:
                            parse_kwargs[param] = request_data[param]

                    try:
                        logger.info(f"發送 parse 請求: {model}, Pydantic 模型: {pydantic_model.__name__}")
                        # 推到背景執行緒並加 30 秒超時
                        completion = await asyncio.wait_for(asyncio.to_thread(client.beta.chat.completions.parse,
                                                                              **parse_kwargs),
                                                            timeout=30)
                        logger.info(f"收到 parse 回應: {completion}")
                        logger.info("成功使用 parse 方法獲取結構化輸出")
                    except asyncio.TimeoutError:
                        logger.error("beta.chat.completions.parse 超時")
                        raise HTTPException(status_code=504, detail="OpenAI parse 請求超時")
                    except Exception as parse_error:
                        logger.error(f"使用 beta.chat.completions.parse 方法失敗: {parse_error}")
                        raise HTTPException(status_code=500, detail=f"無法使用結構化輸出: {parse_error}")

                elif response_format and isinstance(response_format,
                                                    dict) and response_format.get("type") == "json_object":
                    # 原生 OpenAI JSON 模式
                    kwargs = {"model": model, "messages": messages, "response_format": response_format}

                    # 添加其他參數
                    for param in ["temperature", "max_tokens", "top_p", "stream"]:
                        if param in request_data:
                            kwargs[param] = request_data[param]

                    logger.info(f"使用模型: {model}, API 路徑: {self.api_url}")
                    completion = client.chat.completions.create(**kwargs)

                else:
                    # 標準輸出模式
                    kwargs = {"model": model, "messages": messages}

                    # 添加其他參數
                    for param in ["temperature", "max_tokens", "top_p", "stream"]:
                        if param in request_data:
                            kwargs[param] = request_data[param]

                    logger.info(f"使用模型: {model}, API 路徑: {self.api_url}")
                    completion = client.chat.completions.create(**kwargs)

                # 構建回應
                response_data = {
                    "id": completion.id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "finish_reason": completion.choices[0].finish_reason,
                    "status": "completed",
                }

                # 處理結構化輸出
                if is_structured_output:
                    if hasattr(completion.choices[0].message, "parsed"):
                        # 直接從 parse 方法獲取解析後的對象
                        parsed_obj = completion.choices[0].message.parsed
                        logger.info(f"成功解析 '{response_format}' 結構化模型")
                        logger.info(f"解析後的模型: {parsed_obj.model_dump()}")

                        # 添加結構化輸出到響應
                        response_data["structured_output"] = parsed_obj.model_dump()
                        response_data["response_format_type"] = response_format
                    else:
                        # 如果沒有 parsed 屬性，可能是舊版 API 或發生錯誤
                        logger.error("無法獲取解析後的結構化輸出")
                        if hasattr(completion.choices[0].message, "content"):
                            response_data["content"] = completion.choices[0].message.content
                        response_data["error"] = "無法獲取解析後的結構化輸出"
                else:
                    # 非結構化輸出
                    response_data["choices"] = [{
                        "index": 0,
                        "message": {
                            "role": completion.choices[0].message.role,
                            "content": completion.choices[0].message.content
                        },
                        "finish_reason": completion.choices[0].finish_reason
                    }]

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

                    cost = calculate_cost(completion.usage.prompt_tokens, completion.usage.completion_tokens, "openai")
                    self.total_cost += cost

                    logger.info(f"openai請求消耗: {completion.usage.prompt_tokens} 提示 tokens, "
                                f"{completion.usage.completion_tokens} 完成 tokens, 費用: ${cost:.6f}")

                # 記錄請求時間戳
                self.request_timestamps.append(time.time())

                return response_data

            except Exception as e:
                error_str = str(e)
                error_type = type(e).__name__

                # 處理金鑰錯誤：標記無效並嘗試下一個金鑰
                if "authentication" in error_str.lower() or "api key" in error_str.lower(
                ) or "Incorrect API key" in error_str:
                    logger.warning(f"API 金鑰錯誤: {error_str}")
                    await self.key_manager.mark_key_invalid("openai", api_key)
                    logger.warning(f"已標記無效的 OpenAI API 金鑰，嘗試使用其他金鑰 ({len(tried_keys)}/{len(available_keys)})")
                    continue  # 繼續嘗試下一個金鑰

                # 處理模型不存在錯誤
                elif "model" in error_str.lower() and "not exist" in error_str.lower():
                    original_model = model
                    model = self.default_model
                    logger.warning(f"模型 {original_model} 不存在，切換為預設模型 {model}")
                    # 重試時使用相同的金鑰，所以從 tried_keys 中移除
                    if api_key in tried_keys:
                        tried_keys.remove(api_key)
                    continue

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

                # 對於其他錯誤，直接拋出以觸發故障切換
                else:
                    self.error_counts["other"] += 1
                    logger.error(f"OpenAI API 呼叫失敗 ({error_type}): {error_str}")
                    raise HTTPException(status_code=500, detail=f"OpenAI API 呼叫失敗: {error_str}")

        # 如果所有金鑰都嘗試過但仍失敗
        if tried_keys and tried_keys == available_keys:
            logger.error("所有 OpenAI API 金鑰均無效或達到速率限制")
            raise HTTPException(status_code=401, detail="所有 OpenAI API 金鑰均無效或達到速率限制")

    async def _get_best_model_for_structured_output(self) -> str:
        """
        獲取最佳的結構化輸出模型

        Returns:
            str: 最適合用於結構化輸出的模型名稱
        """
        available_models = await self.get_model_list()

        if "gpt-4.1-2025-04-14" in available_models:
            return "gpt-4.1-2025-04-14"
        elif "gpt-4o" in available_models:
            return "gpt-4o"

        elif "gpt-4-turbo" in available_models:
            return "gpt-4-turbo"
        elif "gpt-4" in available_models:
            return "gpt-4"
        elif "gpt-3.5-turbo" in available_models:
            return "gpt-3.5-turbo"
        elif "grok-3" in available_models:
            return "grok-3"
        else:
            # 回退到可用的第一個模型
            return available_models[0] if available_models else self.default_model

    async def _get_models_with_structured_output(self) -> List[str]:
        """
        獲取支援結構化輸出的 OpenAI 模型列表

        Returns:
            List[str]: 支援結構化輸出的模型列表
        """
        # 目前所有 OpenAI 主要模型都支援 JSON 輸出
        return await self.get_model_list()

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
        return "openai"

    async def get_model_list(self) -> List[str]:
        """
        獲取 OpenAI 支援的模型列表

        Returns:
            List[str]: 模型列表
        """
        # OpenAI 常用模型列表
        return [
            "grok-3", "gpt-4.1-2025-04-14", "gpt-4-turbo", "gpt-4", "gpt-4-32k", "gpt-3.5-turbo", "gpt-3.5-turbo-16k"
        ]
