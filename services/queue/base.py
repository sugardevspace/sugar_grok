from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class QueueManager(ABC):
    """佇列管理器抽象基類"""

    @abstractmethod
    async def enqueue(self, request_data: Dict[str, Any], priority: int = 10) -> str:
        """
        將請求添加到佇列
        
        Args:
            request_data: 要排入佇列的請求資料
            priority: 這項請求的優先級(0~100)越小越優先
            
        Returns:
            str: 請求 ID
        """
        pass

    @abstractmethod
    async def priority_enqueue(self, request_item: Dict[str, Any]) -> None:
        """
        將請求添加到佇列的前端（優先處理）
        
        Args:
            request_item: 要排入佇列的請求項目
        """
        pass

    @abstractmethod
    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        從佇列中獲取下一個請求
        
        Returns:
            Optional[Dict[str, Any]]: 請求資料，如佇列為空則返回 None
        """
        pass

    @abstractmethod
    async def get_queue_length(self) -> int:
        """
        獲取當前佇列長度
        
        Returns:
            int: 佇列中的請求數量
        """
        pass

    @abstractmethod
    async def store_response(self, request_id: str, response_data: Dict[str, Any]) -> None:
        """
        儲存請求的回應
        
        Args:
            request_id: 請求 ID
            response_data: 回應資料
        """
        pass

    @abstractmethod
    async def get_response(self, request_id: str) -> Optional[str]:
        """
        獲取請求的回應
        
        Args:
            request_id: 請求 ID
            
        Returns:
            Optional[str]: 回應資料的 JSON 字串，如果找不到回應則返回 None
        """
        pass
