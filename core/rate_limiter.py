import asyncio
import time
from core.logger import logger


class TokenBucketRateLimiter:
    """令牌桶速率限制器"""

    def __init__(self, rate: int):
        """
        初始化令牌桶限制器
        
        Args:
            rate: 每秒允許的請求數
        """
        self.rate = rate  # 每秒允許的請求數
        self.tokens = rate  # 可用令牌數
        self.last_refill = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """
        嘗試獲取令牌
        
        Returns:
            bool: 如果成功獲取令牌則返回 True，否則返回 False
        """
        async with self.lock:
            now = time.time()
            # 重新填充令牌
            elapsed = now - self.last_refill
            new_tokens = elapsed * self.rate
            self.tokens = min(self.rate, self.tokens + new_tokens)
            self.last_refill = now

            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

    # rate_limiter.py 中修改 wait_for_token 方法，添加最大等待時間
    async def wait_for_token(self, max_wait_time: float = 5.0) -> bool:
        """
        等待直到獲取令牌或超時
        
        Args:
            max_wait_time: 最大等待時間（秒）
            
        Returns:
            bool: 是否成功獲取令牌
        """
        start_time = time.time()

        while not await self.acquire():
            # 檢查是否超過最大等待時間
            if time.time() - start_time > max_wait_time:
                logger.warning(f"等待速率限制令牌超過 {max_wait_time} 秒，放棄等待")
                return False

            wait_time = 1.0 / self.rate  # 等待大約一個令牌的重新填充時間
            logger.debug(f"等待速率限制，休眠 {wait_time:.4f} 秒")
            await asyncio.sleep(min(wait_time, 0.5))  # 最多等待 0.5 秒再檢查

        logger.debug("獲取了速率限制令牌")
        return True
