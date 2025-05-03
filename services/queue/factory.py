from core.logger import logger
from services.queue.base import QueueManager
from services.queue.redis_queue import RedisQueueManager
from services.queue.memory_queue import MemoryQueueManager

# 全域佇列管理器實例
_queue_manager = None


def get_queue_manager() -> QueueManager:
    """
    工廠方法：獲取佇列管理器實例
    優先嘗試使用 Redis，如果 Redis 不可用則使用記憶體佇列
    
    Returns:
        QueueManager: 佇列管理器
    """
    global _queue_manager

    if _queue_manager is not None:
        return _queue_manager

    # 嘗試初始化 Redis 佇列
    try:
        logger.info("嘗試初始化 Redis 佇列管理器")
        _queue_manager = RedisQueueManager()
        logger.info("成功初始化 Redis 佇列管理器")
        return _queue_manager
    except Exception as e:
        logger.warning(f"無法初始化 Redis 佇列管理器: {e}")
        logger.info("切換至記憶體佇列管理器")
        _queue_manager = MemoryQueueManager()
        return _queue_manager
