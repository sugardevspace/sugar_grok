import time
import asyncio
from typing import Dict, Any, List, Optional

from core.setting import settings
from core.logger import logger
from services.llm.factory import get_llm_service, create_llm_service


class FailoverManager:
    """LLM 服務故障切換管理器 - 多提供者版本"""

    _instance = None

    def __new__(cls):
        """單例模式實作"""
        if cls._instance is None:
            cls._instance = super(FailoverManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化故障切換管理器"""
        # 讀取主要和備用提供者設定
        self.primary_provider = settings.LLM_PROVIDER

        # 從字串讀取備用提供者列表 (如 "openai,anthropic,local")
        self.failover_providers = self._parse_failover_providers()

        # 當前使用的提供者
        self.current_provider = self.primary_provider

        # 其他設定
        self.enable_failover = settings.ENABLE_FAILOVER
        self.threshold = settings.FAILOVER_THRESHOLD
        self.recovery_time = settings.FAILOVER_RECOVERY_TIME

        # 狀態追蹤
        self.failure_counts = {provider: 0 for provider in self._all_providers()}
        self.provider_statuses = {
            provider: {
                "available": True,
                "last_check": time.time()
            }
            for provider in self._all_providers()
        }
        self.in_failover_mode = False
        self.lock = asyncio.Lock()

        logger.info(f"故障切換管理器初始化完成，主要提供者: {self.primary_provider}, "
                    f"備用提供者: {self.failover_providers}")

    def _parse_failover_providers(self) -> List[str]:
        """解析備用提供者設定"""
        providers_str = settings.FAILOVER_PROVIDERS
        if not providers_str:
            return []

        return [p.strip() for p in providers_str.split(",") if p.strip()]

    def _all_providers(self) -> List[str]:
        """取得所有提供者列表（主要 + 備用）"""
        return [self.primary_provider] + self.failover_providers

    async def get_current_service(self):
        """取得目前應使用的 LLM 服務"""
        try:
            # 添加超時機制，避免鎖爭用
            async with asyncio.timeout(3.0):  # 整體操作最多3秒
                # 嘗試獲取鎖，但設置超時避免無限等待
                try:
                    lock_acquired = await asyncio.wait_for(self.lock.acquire(), 2.0)
                    if not lock_acquired:
                        raise asyncio.TimeoutError("無法獲取鎖")
                except asyncio.TimeoutError:
                    logger.warning("獲取故障切換鎖超時，使用當前提供者而不進行健康檢查")
                    # 超時情況下直接使用當前提供者
                    return self._get_service_without_lock()

                try:
                    # 如果在故障切換模式，且已過恢復檢查時間，嘗試切回主要提供者
                    if self.in_failover_mode:
                        now = time.time()
                        primary_status = self.provider_statuses[self.primary_provider]
                        if now - primary_status["last_check"] > self.recovery_time:
                            await self._check_provider_recovery(self.primary_provider)

                    # 設定當前提供者
                    prev_provider = settings.LLM_PROVIDER
                    settings.LLM_PROVIDER = self.current_provider

                    # 取得對應的服務
                    service = get_llm_service()

                    # 恢復原始設定值
                    settings.LLM_PROVIDER = prev_provider

                    return service
                finally:
                    # 確保釋放鎖
                    self.lock.release()
        except asyncio.TimeoutError:
            logger.warning("get_current_service 整體操作超時，使用當前提供者")
            return self._get_service_without_lock()
        except Exception as e:
            logger.error(f"獲取服務時發生錯誤: {e}")
            return self._get_service_without_lock()

    def _get_service_without_lock(self):
        """不使用鎖的情況下獲取服務（用於超時或錯誤時的後備方案）"""
        prev_provider = settings.LLM_PROVIDER
        settings.LLM_PROVIDER = self.current_provider

        try:
            service = get_llm_service()
        except Exception as e:
            logger.error(f"使用當前提供者獲取服務失敗: {e}")
            # 如果當前提供者失敗，嘗試使用主要提供者
            settings.LLM_PROVIDER = self.primary_provider
            try:
                service = get_llm_service()
            except Exception as e2:
                logger.error(f"使用主要提供者獲取服務也失敗: {e2}")
                # 如果主要提供者也失敗，嘗試另一個提供者
                if self.primary_provider == "openai":
                    settings.LLM_PROVIDER = "grok"
                else:
                    settings.LLM_PROVIDER = "openai"
                service = get_llm_service()
        finally:
            # 確保恢復原始設定值
            settings.LLM_PROVIDER = prev_provider

        return service

    async def report_failure(self, provider: Optional[str] = None):
        """報告 API 呼叫失敗"""
        if not self.enable_failover:
            return

        if provider is None:
            provider = self.current_provider

        try:
            # 添加超時機制
            async with asyncio.timeout(2.0):
                # 嘗試獲取鎖，設置超時
                try:
                    lock_acquired = await asyncio.wait_for(self.lock.acquire(), 1.0)
                    if not lock_acquired:
                        logger.warning(f"報告失敗獲取鎖超時: {provider}")
                        return
                except asyncio.TimeoutError:
                    logger.warning(f"報告失敗獲取鎖超時: {provider}")
                    return

                try:
                    self.failure_counts[provider] += 1
                    logger.warning(f"LLM 服務 {provider} 失敗 ({self.failure_counts[provider]}/{self.threshold})")

                    # 如果達到失敗閾值，將此提供者標記為不可用
                    if self.failure_counts[provider] >= self.threshold:
                        await self._mark_provider_unavailable(provider)

                        # 如果當前提供者不可用，切換到下一個可用提供者
                        if provider == self.current_provider:
                            await self._switch_to_next_available_provider()
                finally:
                    self.lock.release()
        except asyncio.TimeoutError:
            logger.warning(f"報告失敗整體操作超時: {provider}")
        except Exception as e:
            logger.error(f"報告失敗時發生錯誤: {e}")

    async def report_success(self, provider: Optional[str] = None):
        """
        報告 API 呼叫成功
        
        Args:
            provider: 指定的提供者，如果為 None 則使用當前提供者
        """
        if provider is None:
            provider = self.current_provider

        async with self.lock:
            self.failure_counts[provider] = 0

            # 如果提供者之前被標記為不可用，現在標記為可用
            if not self.provider_statuses[provider]["available"]:
                self.provider_statuses[provider]["available"] = True
                logger.info(f"LLM 服務 {provider} 已恢復可用")

                # 如果是主要提供者恢復，切換回主要提供者
                if provider == self.primary_provider and self.current_provider != self.primary_provider:
                    self.current_provider = self.primary_provider
                    self.in_failover_mode = False
                    logger.info(f"切換回主要提供者 {self.primary_provider}")

    async def _mark_provider_unavailable(self, provider: str):
        """將提供者標記為不可用"""
        self.provider_statuses[provider]["available"] = False
        self.provider_statuses[provider]["last_check"] = time.time()
        logger.warning(f"LLM 服務 {provider} 被標記為不可用")

    async def _switch_to_next_available_provider(self):
        """切換到下一個可用的提供者"""
        # 先檢查主要提供者是否可用
        if self.provider_statuses[self.primary_provider]["available"]:
            if self.current_provider != self.primary_provider:
                self.current_provider = self.primary_provider
                self.in_failover_mode = False
                logger.info(f"切換至主要提供者 {self.primary_provider}")
                return

        # 否則，從備用提供者中尋找可用的
        for provider in self.failover_providers:
            if self.provider_statuses[provider]["available"]:
                self.current_provider = provider
                self.in_failover_mode = True
                logger.warning(f"切換至備用提供者 {provider}")
                return

        # 如果沒有可用的提供者，記錄警告並使用主要提供者
        logger.error("沒有可用的 LLM 服務提供者！仍使用主要提供者")
        self.current_provider = self.primary_provider

    async def _check_provider_recovery(self, provider: str):
        """
        檢查提供者是否已恢復
        
        Args:
            provider: 要檢查的提供者
        """
        if self.provider_statuses[provider]["available"]:
            return

        try:
            # 創建指定提供者的服務
            service = create_llm_service(provider)

            # 進行健康檢查
            is_healthy = await service.health_check()

            if is_healthy:
                self.provider_statuses[provider]["available"] = True
                self.failure_counts[provider] = 0
                logger.info(f"LLM 服務 {provider} 已恢復")

                # 如果是主要提供者恢復且當前在故障切換模式，切回主要提供者
                if provider == self.primary_provider and self.in_failover_mode:
                    self.current_provider = self.primary_provider
                    self.in_failover_mode = False
                    logger.info(f"切換回主要提供者 {self.primary_provider}")
            else:
                logger.warning(f"LLM 服務 {provider} 仍未恢復")
        except Exception as e:
            logger.warning(f"檢查 LLM 服務 {provider} 時發生錯誤: {e}")
        finally:
            # 更新最後檢查時間
            self.provider_statuses[provider]["last_check"] = time.time()

    def get_status(self) -> Dict[str, Any]:
        """
        獲取故障切換管理器的狀態
        
        Returns:
            Dict[str, Any]: 狀態資訊
        """
        return {
            "current_provider": self.current_provider,
            "primary_provider": self.primary_provider,
            "failover_providers": self.failover_providers,
            "in_failover_mode": self.in_failover_mode,
            "provider_statuses": {
                provider: {
                    "available": status["available"],
                    "failure_count": self.failure_counts[provider],
                    "last_check": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(status["last_check"]))
                }
                for provider, status in self.provider_statuses.items()
            }
        }


# 單例訪問函數
def get_failover_manager() -> FailoverManager:
    """取得故障切換管理器實例"""
    return FailoverManager()
