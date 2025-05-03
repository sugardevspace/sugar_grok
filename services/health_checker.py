import asyncio
import time
import httpx
from typing import Dict, Any, Optional

from core.setting import settings
from core.logger import logger
from services.failover_manager import get_failover_manager
from services.llm.factory import get_llm_service


class HealthChecker:
    """健康檢查服務，定期檢查各個 LLM 提供者的可用性"""

    _instance = None

    def __new__(cls):
        """單例模式實作"""
        if cls._instance is None:
            cls._instance = super(HealthChecker, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化健康檢查服務"""
        self.failover_manager = get_failover_manager()
        self.check_interval = settings.HEALTH_CHECK_INTERVAL  # 健康檢查間隔（秒）
        self.running = False
        self.task = None
        self.health_endpoints = settings.HEALTH_CHECK_ENDPOINTS

        # 自定義健康檢查處理器
        self.custom_health_checks = {
            # 每個提供者可以有自己特定的健康檢查邏輯
            "grok": self._check_grok_health,
            "openai": self._check_openai_health,
        }

        logger.info("健康檢查服務初始化完成")

    async def start(self):
        """啟動健康檢查服務"""
        if self.running:
            logger.warning("健康檢查服務已在運行")
            return

        # 先執行一次初始健康檢查
        await self.initial_health_check()

        self.running = True
        self.task = asyncio.create_task(self._check_loop())
        logger.info("健康檢查服務已啟動")

    async def stop(self):
        """停止健康檢查服務"""
        if not self.running:
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("健康檢查服務已停止")

    async def initial_health_check(self):
        """
        在伺服器啟動時執行一次全面的健康檢查
        """
        logger.info("執行所有提供者的初始健康檢查...")

        providers = self.failover_manager._all_providers()
        results = {}

        for provider in providers:
            try:
                logger.info(f"檢查 {provider} 的初始健康狀況")

                # 使用自定義健康檢查邏輯（如果有）
                if provider in self.custom_health_checks:
                    is_healthy = await self.custom_health_checks[provider]()
                else:
                    # 否則使用通用健康檢查方法
                    is_healthy = await self._general_health_check(provider)

                # 更新提供者狀態
                self.failover_manager.provider_statuses[provider]["last_check"] = time.time(
                )

                if is_healthy:
                    logger.info(f"{provider} 初始健康檢查通過，狀態: 可用")
                    self.failover_manager.provider_statuses[provider]["available"] = True
                    self.failover_manager.failure_counts[provider] = 0
                else:
                    logger.warning(f"{provider} 初始健康檢查失敗，狀態: 不可用")
                    self.failover_manager.provider_statuses[provider]["available"] = False
                    self.failover_manager.failure_counts[provider] += 1

                results[provider] = is_healthy

            except Exception as e:
                logger.error(f"初始檢查 {provider} 健康狀況時發生錯誤: {e}")
                self.failover_manager.provider_statuses[provider]["available"] = False
                self.failover_manager.failure_counts[provider] += 1
                results[provider] = False

        # 如果主要提供者不可用，切換到第一個可用的備用提供者
        if not self.failover_manager.provider_statuses[self.failover_manager.primary_provider]["available"]:
            await self.failover_manager._switch_to_next_available_provider()

        logger.info(f"初始健康檢查完成，結果: {results}")
        return results

    async def _check_loop(self):
        """健康檢查主循環"""
        logger.info(f"健康檢查循環開始，檢查間隔：{self.check_interval} 秒")
        while self.running:
            try:
                providers = self.failover_manager._all_providers()

                for provider in providers:
                    # 只檢查標記為不可用的提供者或長時間未檢查的提供者
                    provider_status = self.failover_manager.provider_statuses[provider]
                    now = time.time()

                    if (not provider_status["available"] or
                            now - provider_status["last_check"] > self.check_interval):
                        await self._check_provider_health(provider)

                # 檢查完所有提供者後休眠一段時間
                await asyncio.sleep(self.check_interval / 2)

            except asyncio.CancelledError:
                logger.info("健康檢查循環被取消")
                break
            except Exception as e:
                logger.error(f"健康檢查循環發生錯誤: {e}")
                await asyncio.sleep(10)  # 錯誤後短暫休眠

    async def _check_provider_health(self, provider: str):
        """
        檢查指定提供者的健康狀況

        Args:
            provider: 提供者名稱
        """
        logger.debug(f"檢查 {provider} 的健康狀況")

        try:
            # 使用自定義健康檢查邏輯（如果有）
            if provider in self.custom_health_checks:
                is_healthy = await self.custom_health_checks[provider]()
            else:
                # 否則使用通用健康檢查方法
                is_healthy = await self._general_health_check(provider)

            # 更新提供者狀態
            self.failover_manager.provider_statuses[provider]["last_check"] = time.time(
            )

            if is_healthy:
                if not self.failover_manager.provider_statuses[provider]["available"]:
                    logger.info(f"{provider} 健康檢查通過，標記為可用")
                    self.failover_manager.provider_statuses[provider]["available"] = True
                    self.failover_manager.failure_counts[provider] = 0

                    # 如果是主要提供者恢復，考慮切換回主要提供者
                    if (provider == self.failover_manager.primary_provider and
                            self.failover_manager.in_failover_mode):
                        logger.info(f"主要提供者 {provider} 已恢復，準備切換回主要提供者")
                        # 不立即切換，留給 failover_manager 在下一次 get_current_service 時處理
            else:
                logger.warning(f"{provider} 健康檢查失敗")
                if self.failover_manager.provider_statuses[provider]["available"]:
                    self.failover_manager.failure_counts[provider] += 1
                    # 如果連續失敗次數達到閾值，標記為不可用
                    if self.failover_manager.failure_counts[provider] >= settings.FAILOVER_THRESHOLD:
                        logger.warning(f"{provider} 連續失敗次數達到閾值，標記為不可用")
                        self.failover_manager.provider_statuses[provider]["available"] = False

                        # 如果當前使用的是此提供者，觸發故障切換
                        if provider == self.failover_manager.current_provider:
                            logger.warning(f"當前使用的提供者 {provider} 不可用，觸發故障切換")
                            await self.failover_manager._switch_to_next_available_provider()

        except Exception as e:
            logger.error(f"檢查 {provider} 健康狀況時發生錯誤: {e}")
            # 出錯時，增加失敗計數
            self.failover_manager.failure_counts[provider] += 1

    async def _general_health_check(self, provider: str) -> bool:
        """
        通用健康檢查方法，嘗試調用提供者的健康檢查 API

        Args:
            provider: 提供者名稱

        Returns:
            bool: 提供者是否健康
        """
        # 檢查是否有設定專用的健康檢查端點
        endpoint = self.health_endpoints.get(provider, "")

        if endpoint:
            # 嘗試直接呼叫健康檢查端點
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(endpoint)
                    return response.status_code == 200
            except Exception as e:
                logger.error(f"通過端點檢查 {provider} 健康狀況失敗: {e}")
                return False

        # 如果沒有健康檢查端點，嘗試實際調用 API
        try:
            # 設定臨時提供者
            prev_provider = settings.LLM_PROVIDER
            settings.LLM_PROVIDER = provider

            # 獲取對應的服務
            service = get_llm_service()

            # 調用健康檢查方法
            is_healthy = await service.health_check()

            # 恢復原始設定
            settings.LLM_PROVIDER = prev_provider

            return is_healthy
        except Exception as e:
            logger.error(f"通過 API 調用檢查 {provider} 健康狀況失敗: {e}")
            # 恢復原始設定
            settings.LLM_PROVIDER = prev_provider
            return False

    # 特定提供者的自定義健康檢查方法

    async def _check_grok_health(self) -> bool:
        """
        Grok 特定的健康檢查邏輯

        Returns:
            bool: Grok 服務是否健康
        """
        # 這裡可以實作 Grok 特定的健康檢查邏輯
        # 例如檢查特定的端點或使用特定的測試案例
        return await self._general_health_check("grok")

    async def _check_openai_health(self) -> bool:
        """
        OpenAI 特定的健康檢查邏輯

        Returns:
            bool: OpenAI 服務是否健康
        """
        # OpenAI 特定的健康檢查邏輯
        return await self._general_health_check("openai")

    async def _check_anthropic_health(self) -> bool:
        """
        Anthropic 特定的健康檢查邏輯

        Returns:
            bool: Anthropic 服務是否健康
        """
        # Anthropic 特定的健康檢查邏輯
        return await self._general_health_check("anthropic")

    async def _check_local_health(self) -> bool:
        """
        本地 LLM 特定的健康檢查邏輯

        Returns:
            bool: 本地 LLM 服務是否健康
        """
        # 本地 LLM 特定的健康檢查邏輯，可以做更徹底的檢查
        endpoint = self.health_endpoints.get("local", "")

        if endpoint:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(endpoint)
                    return response.status_code == 200
            except Exception:
                return False

        return await self._general_health_check("local")

# 單例訪問函數


def get_health_checker() -> HealthChecker:
    """取得健康檢查服務實例"""
    return HealthChecker()
