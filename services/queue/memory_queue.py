import json
import time
import os
import asyncio
from typing import Dict, Any, Optional

from core.logger import logger
from services.queue.base import QueueManager


class MemoryQueueManager(QueueManager):
    """記憶體內佇列管理器實作"""

    def __init__(self):
        """初始化記憶體佇列"""
        self.queue = asyncio.Queue()
        self.responses = {}  # request_id -> response_data
        logger.info("初始化記憶體佇列")

    async def enqueue(self, request_data: Dict[str, Any], priority: int = 10) -> str:
        """
        將請求添加到記憶體佇列
        
        Args:
            request_data: 要排入佇列的請求資料
            
        Returns:
            str: 請求 ID
        """
        # 產生唯一請求 ID
        request_id = f"req_{int(time.time() * 1000)}_{os.urandom(4).hex()}"

        # 將請求資料添加到佇列
        await self.queue.put({"id": request_id, "data": request_data, "timestamp": time.time()})

        logger.debug(f"已將請求 {request_id} 加入記憶體佇列")
        return request_id

    async def priority_enqueue(self, request_item: Dict[str, Any]) -> None:
        """
        將請求添加到記憶體佇列前端（優先處理）
        
        Args:
            request_item: 要排入佇列的請求項目
        """
        # 建立新佇列，先放入新項目，再放入原佇列項目
        old_queue = self.queue
        self.queue = asyncio.Queue()

        # 先加入優先項目
        await self.queue.put(request_item)

        # 移動原佇列項目
        while not old_queue.empty():
            try:
                item = old_queue.get_nowait()
                await self.queue.put(item)
            except asyncio.QueueEmpty:
                break

        logger.debug(f"已將請求 {request_item.get('id')} 加入記憶體佇列前端（優先）")

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        從記憶體佇列中獲取下一個請求
        
        Returns:
            Optional[Dict[str, Any]]: 請求資料，如佇列為空則返回 None
        """
        try:
            # 嘗試從佇列取出項目，設置超時防止無限等待
            request_item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            logger.debug(f"從記憶體佇列取出請求 {request_item.get('id')}")
            return request_item
        except asyncio.TimeoutError:
            return None

    async def get_queue_length(self) -> int:
        """
        獲取當前記憶體佇列長度
        
        Returns:
            int: 佇列中的請求數量
        """
        return self.queue.qsize()

    async def store_response(self, request_id: str, response_data: Dict[str, Any]) -> None:
        """
        在記憶體中儲存請求的回應
        
        Args:
            request_id: 請求 ID
            response_data: 回應資料
        """
        self.responses[request_id] = json.dumps(response_data)
        logger.debug(f"已將請求 {request_id} 的回應儲存到記憶體")

        # 設置一個清理任務，模擬 Redis 的過期時間
        asyncio.create_task(self._cleanup_response(request_id, 3600))  # 1 小時後清理

    async def _cleanup_response(self, request_id: str, delay: int) -> None:
        """
        延遲後清理回應資料
        
        Args:
            request_id: 請求 ID
            delay: 延遲秒數
        """
        await asyncio.sleep(delay)
        if request_id in self.responses:
            del self.responses[request_id]
            logger.debug(f"清理了請求 {request_id} 的過期回應")

    async def get_response(self, request_id: str) -> Optional[str]:
        """
        從記憶體獲取請求的回應
        
        Args:
            request_id: 請求 ID
            
        Returns:
            Optional[str]: 回應資料的 JSON 字串，如果找不到回應則返回 None
        """
        response_data = self.responses.get(request_id)

        if response_data:
            logger.debug(f"從記憶體獲取請求 {request_id} 的回應")
            return response_data

        logger.debug(f"在記憶體中找不到請求 {request_id} 的回應")
        return None
