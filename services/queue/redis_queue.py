import asyncio
import json
import time
import os
import redis
from typing import Dict, Any, Optional

import redis.exceptions

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
            self.memory_queue = None
            self.background_ping = None
            self.memory_queue_lock = asyncio.Lock()
            self.background_ping_lock = asyncio.Lock()

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

    async def _execute_with_error_handing(
        self,
        redis_func,
        *args,
        fallback_to_memory: bool = True,
        **kwargs
    ) -> Any:
        """
        內部方法
        對任何 Redis 函式進行「重試 + 重建連線 + 最後降級」的通用邏輯。

        Args:
          redis_func: 一個同步函式 (e.g. self.redis.zadd, self.redis.get, self.redis.setex 等)
          *args, **kwargs: 傳給 redis_func 的參數
          fallback_to_memory: 如果全部重試都失敗，是否降級到 memory_queue

        Returns:
          如果 redis_func 執行成功，就回傳它的結果 (任何值)
          如果重試用盡且 fallback_to_memory=True，先執行 memory_queue 相關邏輯，再回傳 False
          如果重試用盡且 fallback_to_memory=False，就直接拋例外
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 1) 先確保 ping
                await asyncio.to_thread(self.redis.ping)
                # 2) 再呼叫真正的 Redis 函式 (包在 to_thread)
                return await asyncio.to_thread(redis_func, *args, **kwargs)

            except redis.exceptions.ConnectionError as e:
                logger.warning(f"Redis 連接錯誤 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    # 如果還沒到最後一次，重建連線後繼續下一輪重試
                    try:
                        self.redis = redis.Redis(
                            host=settings.REDIS_HOST,
                            port=settings.REDIS_PORT,
                            db=settings.REDIS_DB,
                            decode_responses=True,
                            socket_timeout=5.0,  # 添加超時設定
                            socket_connect_timeout=5.0  # 連接超時設定
                        )
                        await asyncio.sleep(0.5 * (attempt + 1))  # 退讓一段時間
                    except Exception as conn_err:
                        logger.error(f"Redis 重新連接失敗: {conn_err}")
                else:
                    # 最後一次重試仍然連不上
                    logger.error("Redis 連接重試次數用盡")
                    if fallback_to_memory:
                        # 降級到記憶體佇列
                        logger.warning("降級到 memory_queue")
                        try:
                            async with self.memory_queue_lock:
                                if self.memory_queue is None:
                                    from services.queue.memory_queue import MemoryQueueManager
                                    self.memory_queue = MemoryQueueManager()
                            async with self.background_ping_lock:
                                if self.background_ping is None:
                                    self.background_ping = asyncio.create_task(self._background_sync())
                            return False
                        except Exception as fallback_error:
                            logger.critical(f"降級到內存佇列也失敗: {fallback_error}")
                            raise
                    else:
                        raise

            except Exception as e:
                # 非 ConnectionError 的其他錯誤，也給它重試機會
                logger.error(f"Redis 操作失敗 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.critical("Redis 操作重試次數用盡，拋例外")
                    raise

        # 理論上不會到這裡
        raise RuntimeError("Redis 操作執行邏輯錯誤")

    async def _enqueue_with_error_handling(self, request_data: Dict[str, Any], priority: int) -> bool:
        """
        內部方法

        Args:
            request_data: 已經包好的資料包
            priority: 已經處理好的優先級
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 檢查 Redis 連接
                await asyncio.to_thread(self.redis.ping)
                await asyncio.to_thread(self.redis.zadd, self.queue_key, {json.dumps(request_data): priority})
                return True
            except redis.exceptions.ConnectionError as e:
                # Redis 連接錯誤，嘗試重新連接
                logger.warning(f"Redis 連接錯誤 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:  # 不是最後一次嘗試
                    try:
                        # 重新初始化連接
                        self.redis = await asyncio.to_thread(redis.Redis, host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB, decode_responses=True, socket_timeout=5.0, socket_connect_timeout=5.0)
                        await asyncio.sleep(0.5 * (attempt + 1))  # 逐步增加等待時間
                    except Exception as conn_err:
                        logger.error(f"Redis 重新連接失敗: {conn_err}")
                else:
                    # 最後一次嘗試也失敗，降級到內存隊列
                    logger.error("Redis 連接重試次數用盡，嘗試降級到內存佇列")
                    try:
                        self.fall_back_memory_queue()
                        return False
                    except Exception as fallback_err:
                        logger.critical(f"降級到內存佇列也失敗: {fallback_err}")
                        raise

            except Exception as e:
                logger.error(f"Redis 操作失敗 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    # 所有嘗試都失敗
                    logger.critical("Redis 操作重試次數用盡")
                    raise

        # 理論上不應該到達這裡
        raise RuntimeError("Redis 操作失敗且未正確處理")

    async def enqueue(self, request_data: Dict[str, Any], priority: int = 10) -> str:
        """
        將請求添加到 Redis 佇列

        Args:
            priority: 這項請求的優先級(0~100)越小越優先
        """
        if self.memory_queue is not None:
            try:
                return await self.memory_queue.enqueue(request_data, priority)
            except Exception as e:
                logger.warning(f"降級操作失效: {e}")
                return self.enqueue(request_data, priority)

        # 產生唯一請求 ID
        request_id = f"req_{int(time.time() * 1000)}_{os.urandom(4).hex()}"

        # 檢查優先級範圍
        priority = max(0, min(100, priority))

        payload = {
            "id": request_id,
            "data": request_data,
            "timestamp": time.time(),
            "priority": priority
        }
        # 當有同優先級的存在，先處裡早到的
        ts_ms = int(payload["timestamp"] * 1000)
        composite_score = priority * (10**13) + ts_ms

        # 將請求資料添加到佇列

        if await self._execute_with_error_handing(self.redis.zadd, self.queue_key, {json.dumps(payload): composite_score}):
            logger.debug(f"已將請求 {request_id} 加入 Redis 佇列")
            return request_id
        else:
            logger.debug(f"已將請求 {request_id} 重新發送入 enqueue")
            return await self.enqueue(request_data, priority)

    async def priority_enqueue(self, request_item: Dict[str, Any]) -> None:
        """
        將請求添加到 Redis 佇列前端（優先處理）

        Args:
            request_item: 要排入佇列的請求項目
        """
        # 降級操作
        if self.memory_queue is not None:
            try:
                return await self.memory_queue.priority_enqueue(request_item)
            except Exception as e:
                logger.warning(f"降級操作失效: {e}")
                return await self.priority_enqueue(request_item)

        if await self._execute_with_error_handing(self.redis.zadd, self.queue_key, {json.dumps(request_item): request_item["timestamp"]}):
            logger.debug(f"已將請求 {request_item.get('id')} 加入 Redis 佇列前端（優先）")
        else:
            logger.info(f"已將請求 {request_item.get('id')} 重新發送入 priority_enqueue")
            return await self.priority_enqueue(request_item)

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        從 Redis 佇列中獲取下一個請求

        Returns:
            Optional[Dict[str, Any]]: 請求資料，如佇列為空則返回 None
        """
        # 降級操作
        if self.memory_queue is not None:
            try:
                return await self.memory_queue.dequeue()
            except Exception as e:
                logger.warning(f"降級操作失效: {e}")
                return await self.dequeue()

        # 使用 zpopmin 從佇列頂端取出一個項目
        data = await self._execute_with_error_handing(self.redis.zpopmin, self.queue_key)

        if data:
            # 因為回傳的是reponstT格式的資料，所以要拆兩層[("我要的",優先級)]
            request_item = json.loads(data[0][0])
            logger.debug(f"從 Redis 佇列取出請求 {request_item.get('id')}")
            return request_item

        return None

    async def get_queue_length(self) -> int:
        """
        獲取當前 Redis 佇列長度

        Returns:
            int: 佇列中的請求數量
        """
        if self.memory_queue is not None:
            try:
                return await self.memory_queue.get_queue_length()
            except Exception as e:
                logger.warning(f"降級操作失效: {e}")
                return await self.get_queue_length()
        else:
            return await self._execute_with_error_handing(self.redis.zcard, self.queue_key)

    async def store_response(self, request_id: str, response_data: Dict[str, Any]) -> None:
        """
        在 Redis 中儲存請求的回應

        Args:
            request_id: 請求 ID
            response_data: 回應資料
        """

        # 降級操作
        if self.memory_queue is not None:
            try:
                return await self.memory_queue.store_response(request_id, response_data)
            except Exception as e:
                logger.warning(f"降級操作失效: {e}")
                return await self.store_response(request_id, response_data)

        response_key = f"{self.response_prefix}{request_id}"
        await self._execute_with_error_handing(self.redis.setex, response_key, self.response_expiry, json.dumps(response_data))
        logger.debug(f"已將請求 {request_id} 的回應儲存到 Redis")

    async def get_response(self, request_id: str) -> Optional[str]:

        # 降級操作
        if self.memory_queue is not None:
            try:
                return await self.memory_queue.get_response(request_id)
            except Exception as e:
                logger.warning(f"降級操作失效: {e}")
                return await self.get_response(request_id)

        response_key = f"{self.response_prefix}{request_id}"

        try:
            # 1) to_thread 讓 redis.get() 在背景線程跑，不會堵塞 event loop
            # 2) wait_for 給它 3 秒鐘的最大等待時間
            response_data = await asyncio.wait_for(self._execute_with_error_handing(self.redis.get, response_key), timeout=3.0)

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

    async def _background_sync(self):
        """
        背景協程：定期檢測 Redis，並將 memory_queue 的所有 item 回寫到 Redis，最後清空 memory_queue。
        1) 初始時先等一秒（避免剛降級時過快 ping 導致不必要的失敗）；
        2) 之後每次 ping 成功就執行同步、重置 backoff；ping 失敗就等待 backoff 秒再試。
        """
        backoff = 1  # 起始等待 1 秒
        # 小小 Sleep，確保 enqueue/dequeue 先把可能要 enqueue 的資料放進來
        await asyncio.sleep(1)
        logger.info("升級檢查啟動")
        while True:
            try:
                # 嘗試 ping Redis
                await asyncio.to_thread(self.redis.ping)
                # 如果 ping 成功，且 memory_queue 裡有東西，逐一寫回去
                if self.memory_queue is not None:
                    memory_queue = self.memory_queue
                    self.memory_queue = None

                    item = await memory_queue.dequeue()
                    while item is not None:
                        score = int(time.time() * 1000)
                        score += 10**14
                        logger.info(item)
                        await asyncio.to_thread(self.redis.zadd, self.queue_key, {json.dumps(item): score})
                        item = await memory_queue.dequeue()
                    logger.info("background_sync: memory_queue 全部寫回 Redis，並清空 memory_queue")
                    self.background_ping.cancel()
                    self.background_ping = None
                    return

            except redis.exceptions.ConnectionError:
                # Redis 還是 down，等待 backoff 秒再試
                logger.warning(f"background_sync: Redis 還是連不上，{backoff} 秒後再試")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)  # 指數退避，最多 10 秒

            except Exception as e:
                # 其他意外也不要讓協程掛掉，只 log 並稍後重試
                logger.error(f"background_sync: 非預期錯誤: {e}")
                await asyncio.sleep(5)
