import asyncio
import json
import time
import os
import redis
from typing import Dict, Any, Optional

from core.setting import settings
from core.logger import logger
from services.queue.base import QueueManager


class RedisQueueManager(QueueManager):
    """Redis 佇列管理器實作"""

    def __init__(self):
        """初始化 Redis 連接"""
        try:
            self.redis = redis.Redis(host=settings.REDIS_HOST,
                                     port=settings.REDIS_PORT,
                                     db=settings.REDIS_DB,
                                     decode_responses=True)
            self.queue_key = settings.REDIS_QUEUE_KEY
            self.response_prefix = settings.REDIS_RESPONSE_PREFIX
            self.response_expiry = settings.REDIS_RESPONSE_EXPIRY

            # 檢查連接
            self.redis.ping()
            logger.info(f"已成功連接到 Redis ({settings.REDIS_HOST}:{settings.REDIS_PORT})")
        except Exception as e:
            logger.error(f"無法連接到 Redis: {e}")
            raise

    # redis_queue.py 中添加 Redis 連接重試機制
    def get_redis_connection(self):
        """獲取 Redis 連接，如果斷開則重新連接"""
        try:
            # 嘗試 ping 以檢查連接是否有效
            self.redis.ping()
            return self.redis
        except Exception as e:
            logger.warning(f"Redis 連接失敗，嘗試重新連接: {e}")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self.redis = redis.Redis(
                        host=settings.REDIS_HOST,
                        port=settings.REDIS_PORT,
                        db=settings.REDIS_DB,
                        decode_responses=True,
                        socket_timeout=5.0,  # 添加超時設定
                        socket_connect_timeout=5.0  # 連接超時設定
                    )
                    self.redis.ping()
                    logger.info("Redis 重新連接成功")
                    return self.redis
                except Exception as retry_err:
                    logger.error(f"Redis 重新連接嘗試 {attempt + 1}/{max_retries} 失敗: {retry_err}")
                    if attempt < max_retries - 1:
                        time.sleep(1)  # 等待一秒再重試

            # 如果所有重試都失敗，拋出異常
            raise ConnectionError("無法連接到 Redis 服務器")

    async def enqueue(self, request_data: Dict[str, Any]) -> str:
        """
        將請求添加到 Redis 佇列，添加錯誤處理和重試
        """
        # 產生唯一請求 ID
        request_id = f"req_{int(time.time() * 1000)}_{os.urandom(4).hex()}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 檢查 Redis 連接
                self.redis.ping()

                # 將請求資料添加到佇列
                self.redis.rpush(self.queue_key,
                                 json.dumps({
                                     "id": request_id,
                                     "data": request_data,
                                     "timestamp": time.time()
                                 }))

                logger.debug(f"已將請求 {request_id} 加入 Redis 佇列")
                return request_id

            except redis.exceptions.ConnectionError as e:
                # Redis 連接錯誤，嘗試重新連接
                logger.warning(f"Redis 連接錯誤 (嘗試 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:  # 不是最後一次嘗試
                    try:
                        # 重新初始化連接
                        self.redis = redis.Redis(host=settings.REDIS_HOST,
                                                 port=settings.REDIS_PORT,
                                                 db=settings.REDIS_DB,
                                                 decode_responses=True,
                                                 socket_timeout=5.0,
                                                 socket_connect_timeout=5.0)
                        await asyncio.sleep(0.5 * (attempt + 1))  # 逐步增加等待時間
                    except Exception as conn_err:
                        logger.error(f"Redis 重新連接失敗: {conn_err}")
                else:
                    # 最後一次嘗試也失敗，降級到內存隊列
                    logger.error("Redis 連接重試次數用盡，嘗試降級到內存佇列")
                    try:
                        from services.queue.memory_queue import MemoryQueueManager
                        memory_queue = MemoryQueueManager()
                        return await memory_queue.enqueue(request_data)
                    except Exception as fallback_err:
                        logger.critical(f"降級到內存佇列也失敗: {fallback_err}")
                        raise

            except Exception as e:
                logger.error(f"Redis 操作失敗 (嘗試 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    # 所有嘗試都失敗
                    logger.critical("Redis 操作重試次數用盡")
                    raise

        # 理論上不應該到達這裡
        raise RuntimeError("Redis 操作失敗且未正確處理")

    async def priority_enqueue(self, request_item: Dict[str, Any]) -> None:
        """
        將請求添加到 Redis 佇列前端（優先處理）
        
        Args:
            request_item: 要排入佇列的請求項目
        """
        self.redis.lpush(self.queue_key, json.dumps(request_item))
        logger.debug(f"已將請求 {request_item.get('id')} 加入 Redis 佇列前端（優先）")

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        從 Redis 佇列中獲取下一個請求
        
        Returns:
            Optional[Dict[str, Any]]: 請求資料，如佇列為空則返回 None
        """
        # 使用 LPOP 從佇列頭部取出一個項目
        data = self.redis.lpop(self.queue_key)

        if data:
            request_item = json.loads(data)
            logger.debug(f"從 Redis 佇列取出請求 {request_item.get('id')}")
            return request_item

        return None

    async def get_queue_length(self) -> int:
        """
        獲取當前 Redis 佇列長度
        
        Returns:
            int: 佇列中的請求數量
        """
        return self.redis.llen(self.queue_key)

    async def store_response(self, request_id: str, response_data: Dict[str, Any]) -> None:
        """
        在 Redis 中儲存請求的回應
        
        Args:
            request_id: 請求 ID
            response_data: 回應資料
        """
        response_key = f"{self.response_prefix}{request_id}"
        self.redis.setex(
            response_key,
            self.response_expiry,  # 設置過期時間
            json.dumps(response_data))
        logger.debug(f"已將請求 {request_id} 的回應儲存到 Redis")

    async def get_response(self, request_id: str) -> Optional[str]:
        response_key = f"{self.response_prefix}{request_id}"

        try:
            # 1) to_thread 讓 redis.get() 在背景線程跑，不會堵塞 event loop
            # 2) wait_for 給它 3 秒鐘的最大等待時間
            response_data = await asyncio.wait_for(asyncio.to_thread(self.redis.get, response_key), timeout=3.0)

            if response_data:
                logger.debug(f"從 Redis 獲取請求 {request_id} 的回應")
                return response_data

            logger.debug(f"在 Redis 中找不到請求 {request_id} 的回應")
            return None

        except asyncio.TimeoutError:
            logger.warning(f"獲取請求 {request_id} 回應超時")
            return None

        except redis.exceptions.ConnectionError as e:
            logger.error(f"獲取回應時 Redis 連接錯誤: {e}")
            return None

        except Exception as e:
            logger.error(f"獲取回應時發生錯誤: {e}")
            return None
