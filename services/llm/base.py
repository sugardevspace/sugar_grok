from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import time
import asyncio

from fastapi import HTTPException

from core.logger import logger
from services.metrics_service import get_metrics_service


class LLMService(ABC):
    """
    大型語言模型服務抽象基類
    提供統一的介面來呼叫不同的 LLM API
    """

    @abstractmethod
    async def call_api(self, request_data: Dict[str, Any], request_id: Optional[str] = None) -> Dict[str, Any]:
        """
        呼叫 LLM API 並處理重試邏輯

        Args:
            request_data: API 請求資料
            request_id: 請求 ID（用於指標追蹤）

        Returns:
            Dict[str, Any]: API 回應

        Raises:
            HTTPException: API 呼叫失敗時
        """
        pass

    async def call_api_with_metrics(self, request_data: Dict[str, Any], request_id: str,
                                    provider: str) -> Dict[str, Any]:
        """
        帶指標追蹤的 API 呼叫

        Args:
            request_data: API 請求資料
            request_id: 請求 ID
            provider: 提供者名稱

        Returns:
            Dict[str, Any]: API 回應
        """
        # 獲取指標服務
        try:
            metrics_service = get_metrics_service()

            # 記錄請求開始
            await metrics_service.record_request(provider, request_id, request_data)

            # 記錄開始時間
            start_time = time.time()

            # 呼叫 API
            try:
                response = await self.call_api(request_data, request_id)
                success = True
            except Exception as e:
                response = {"error": {"message": str(e), "type": "api_error"}}
                success = False
                raise
            finally:
                # 計算處理時間
                duration = time.time() - start_time

                # 提取 token 使用量和費用
                prompt_tokens = None
                completion_tokens = None
                cost = None

                if success and "usage" in response:
                    usage = response["usage"]
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

                    from utils.cost_calculator import calculate_cost
                    cost = calculate_cost(prompt_tokens, completion_tokens, provider)

                # 記錄請求完成
                await metrics_service.record_response(provider, request_id, success, duration, prompt_tokens,
                                                      completion_tokens, cost)

            return response

        except ImportError:
            # 如果指標服務不可用，直接呼叫 API
            logger.warning("指標服務不可用，直接呼叫 API")
            return await self.call_api(request_data, request_id)

    async def health_check(self) -> bool:
        """
        檢查 OpenAI 服務健康狀況

        Returns:
            bool: 服務是否健康
        """
        try:
            # 準備一個簡單的健康檢查請求
            test_request = {
                "model": self.default_model,  # 使用預設模型
                "messages": [{
                    "role": "user",
                    "content": "回覆「成功」"
                }],
                "response_format": "health_check"
            }

            try:
                # 嘗試呼叫 API，設定較短的超時時間
                async with asyncio.timeout(5.0):  # 5 秒超時
                    response = await self.call_api(test_request)

                # 檢查回應是否符合預期
                if response.get("status") == "completed":
                    return True

                logger.warning("健康檢查回應不符合預期")
                return False

            except asyncio.TimeoutError:
                logger.error("健康檢查超時")
                return False

            except HTTPException as e:
                # 處理 API 特定的錯誤
                if e.status_code == 429:  # 速率限制
                    logger.warning(f"健康檢查觸及速率限制: {e.detail}")
                    return False
                elif e.status_code == 401:  # 驗證失敗
                    logger.error(f"API 金鑰驗證失敗: {e.detail}")
                    return False
                else:
                    logger.error(f"健康檢查 API 異常: {e.detail}")
                    return False

        except Exception as e:
            # 捕獲其他未預期的異常
            logger.error(f"健康檢查發生未預期錯誤: {e}")
            return False

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """
        獲取 API 使用統計

        Returns:
            Dict[str, Any]: 使用統計資訊
        """
        pass

    @property
    @abstractmethod
    def default_model(self) -> str:
        """
        提供者的預設模型

        Returns:
            str: 預設模型名稱
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        提供者名稱

        Returns:
            str: 提供者名稱
        """
        pass

    @abstractmethod
    async def get_model_list(self) -> List[str]:
        """
        獲取提供者支援的模型列表

        Returns:
            List[str]: 模型列表
        """
        pass
