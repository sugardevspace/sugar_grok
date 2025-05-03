import asyncio
import time
from typing import Dict, List, Any, Optional
from datetime import datetime

from core.setting import settings
from core.logger import logger


class APIKeyManager:
    """多供應商 API 金鑰管理器"""

    _instance = None

    def __new__(cls):
        """單例模式實作"""
        if cls._instance is None:
            cls._instance = super(APIKeyManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化金鑰管理器"""
        # 為每個供應商初始化金鑰
        self.provider_keys = {
            "grok": self._parse_keys(settings.GROK_API_KEYS),
            "openai": self._parse_keys(settings.OPENAI_API_KEYS),
        }
        self.key_timestamps = {
            provider: {
                key: [] for key in keys
            } for provider, keys in self.provider_keys.items()
        }

        # 每個供應商的當前金鑰索引
        self.current_key_index = {
            provider: 0 for provider in self.provider_keys}

        # 金鑰使用統計
        self.key_usage = {}
        for provider, keys in self.provider_keys.items():
            self.key_usage[provider] = {
                key: {"last_used": 0, "count": 0} for key in keys
            }

        # 同步鎖
        self.lock = asyncio.Lock()

        self.RATE_LIMIT_RPS = settings.RATE_LIMIT_RPS

        logger.info(f"API 金鑰管理器初始化完成，供應商: {list(self.provider_keys.keys())}")

    def _parse_keys(self, keys_input) -> List[str]:
        """解析金鑰輸入（支援字串和列表）"""
        if isinstance(keys_input, str):
            return [key.strip() for key in keys_input.split(",") if key.strip()]
        elif isinstance(keys_input, list):
            return [key for key in keys_input if key]
        return []

    async def get_next_key(self, provider: str) -> str:
        """
        獲取指定提供者的下一個可用 API 金鑰（加入速率限制）

        Args:
            provider: 提供者名稱

        Returns:
            str: API 金鑰

        Raises:
            ValueError: 找不到提供者或金鑰
        """
        if provider not in self.provider_keys:
            logger.error(f"未知的提供者: {provider}")
            raise ValueError(f"未知的提供者: {provider}")

        keys = self.provider_keys[provider]
        if not keys:
            logger.error(f"未找到 {provider} 的 API 金鑰")
            raise ValueError(f"未找到 {provider} 的 API 金鑰")

        while True:
            async with self.lock:
                now = time.time()
                for _ in range(len(keys)):  # 嘗試所有 key
                    index = self.current_key_index[provider]
                    key = keys[index]

                    # 初始化 timestamp queue 如果尚未建立
                    if not hasattr(self, "key_timestamps"):
                        self.key_timestamps = {
                            p: {k: [] for k in self.provider_keys[p]} for p in self.provider_keys
                        }

                    timestamps = self.key_timestamps[provider][key]
                    # 清除超過 1 秒的 timestamp
                    timestamps = [ts for ts in timestamps if now - ts <= 1.0]

                    if len(timestamps) < self.RATE_LIMIT_RPS:
                        # 更新使用記錄與統計
                        timestamps.append(now)
                        self.key_timestamps[provider][key] = timestamps

                        self.key_usage[provider][key]["last_used"] = now
                        self.key_usage[provider][key]["count"] += 1

                        self.current_key_index[provider] = (
                            index + 1) % len(keys)

                        logger.debug(
                            f"使用 {provider} API 金鑰 #{index} - {self._mask_key(key)}")
                        return key

                    # 試下一把 key
                    self.current_key_index[provider] = (index + 1) % len(keys)

            # 如果所有 key 都滿載，稍微等待再試
            logger.warning(f"所有 {provider} 的 API 金鑰達到速率限制，等待 100ms 重試")
            await asyncio.sleep(0.1)

    def get_key_stats(self, provider: Optional[str] = None) -> Dict[str, Any]:
        """
        獲取 API 金鑰使用統計

        Args:
            provider: 指定提供者（如果為 None，則返回所有提供者的統計）

        Returns:
            Dict: 金鑰使用統計
        """
        if provider:
            if provider not in self.key_usage:
                return {}

            return {
                f"{provider}_{i}": {
                    "key": self._mask_key(key),
                    "usage_count": stats["count"],
                    "last_used": datetime.fromtimestamp(stats["last_used"]).isoformat()
                    if stats["last_used"] > 0 else "Never"
                }
                for i, (key, stats) in enumerate(self.key_usage[provider].items())
            }
        else:
            # 返回所有提供者的統計
            all_stats = {}
            for provider, key_stats in self.key_usage.items():
                for i, (key, stats) in enumerate(key_stats.items()):
                    all_stats[f"{provider}_{i}"] = {
                        "key": self._mask_key(key),
                        "provider": provider,
                        "usage_count": stats["count"],
                        "last_used": datetime.fromtimestamp(stats["last_used"]).isoformat()
                        if stats["last_used"] > 0 else "Never"
                    }
            return all_stats

    def _mask_key(self, key: str) -> str:
        """遮蔽 API 金鑰以保護隱私"""
        if not key or len(key) < 8:
            return "****"

        return key[:4] + "..." + key[-4:]

    def add_key(self, provider: str, key: str) -> bool:
        """
        為指定提供者添加新的 API 金鑰

        Args:
            provider: 提供者名稱
            key: API 金鑰

        Returns:
            bool: 是否成功添加
        """
        if not key:
            return False

        if provider not in self.provider_keys:
            self.provider_keys[provider] = []
            self.current_key_index[provider] = 0
            self.key_usage[provider] = {}

        # 檢查金鑰是否已存在
        if key in self.provider_keys[provider]:
            return False

        self.provider_keys[provider].append(key)
        self.key_usage[provider][key] = {"last_used": 0, "count": 0}
        logger.info(f"為 {provider} 添加了新的 API 金鑰")
        return True

    def remove_key(self, provider: str, key: str) -> bool:
        """
        移除指定提供者的 API 金鑰

        Args:
            provider: 提供者名稱
            key: API 金鑰

        Returns:
            bool: 是否成功移除
        """
        if provider not in self.provider_keys or key not in self.provider_keys[provider]:
            return False

        self.provider_keys[provider].remove(key)
        if key in self.key_usage[provider]:
            del self.key_usage[provider][key]

        logger.info(f"從 {provider} 移除了一個 API 金鑰")
        return True

# 單例訪問函數


def get_key_manager() -> APIKeyManager:
    """取得 API 金鑰管理器實例"""
    return APIKeyManager()
