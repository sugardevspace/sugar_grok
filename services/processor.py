import asyncio
from typing import Dict, Any

from core.logger import logger
from core.rate_limiter import TokenBucketRateLimiter
from core.setting import settings
from services.llm.factory import get_llm_service
from services.queue.factory import get_queue_manager
from services.failover_manager import get_failover_manager

# 速率限制器
rate_limiter = TokenBucketRateLimiter(settings.RATE_LIMIT_RPS)
# 佇列管理器
queue_manager = get_queue_manager()
# 故障切換管理器
failover_manager = get_failover_manager()


async def process_queue_item(request_item: Dict[str, Any]) -> None:
    """
    處理單個佇列項目

    Args:
        request_item: 佇列項目
    """
    request_data = request_item["data"]
    request_id = request_item["id"]

    # 追蹤嘗試過的提供者，避免重複嘗試同一提供者
    tried_providers = request_item.get("tried_providers", [])
    retry_count = request_item.get("retry_count", 0)
    max_retries = len(settings.FAILOVER_PROVIDERS.split(",")) + 1  # 主要 + 所有備用

    # 記錄原始模型，用於日誌
    original_model = request_data.get('model')
    original_provider = request_item.get("original_provider", failover_manager.current_provider)

    # 如果是首次處理，記錄原始提供者
    if not tried_providers:
        request_item["original_provider"] = original_provider

    try:
        logger.info(f"開始處理請求 {request_id} (重試: {retry_count}/{max_retries})")

        # 取得當前應使用的 LLM 服務
        llm_service = await failover_manager.get_current_service()
        current_provider = failover_manager.current_provider

        # 如果已經嘗試過這個提供者，嘗試選擇另一個提供者
        if current_provider in tried_providers and retry_count < max_retries:
            for provider in failover_manager._all_providers():
                if provider not in tried_providers and failover_manager.provider_statuses[provider]["available"]:
                    # 設定臨時提供者
                    prev_provider = settings.LLM_PROVIDER
                    settings.LLM_PROVIDER = provider
                    llm_service = get_llm_service()
                    current_provider = provider
                    settings.LLM_PROVIDER = prev_provider
                    break

        # 記錄此次使用的提供者
        tried_providers.append(current_provider)
        logger.info(f"使用 {current_provider} 提供者處理請求 {request_id}")

        # 重要：如果提供者已變更，調整模型
        if 'model' in request_data and current_provider != original_provider:
            # 複製請求數據以避免修改原始數據
            request_data = request_data.copy()

            # 使用目標提供者的默認模型
            if current_provider == "grok":
                request_data['model'] = llm_service.default_model
            elif current_provider == "openai":
                request_data['model'] = llm_service.default_model
            # 添加其他提供者的默認模型...

            logger.info(f"提供者從 {original_provider} 變更為 {current_provider}，"
                        f"模型從 {original_model} 調整為 {request_data['model']}")

        # 調用 LLM API (使用帶指標的方法)
        response = await llm_service.call_api_with_metrics(request_data, request_id, current_provider)

        # 報告成功
        await failover_manager.report_success(current_provider)

        # 儲存回應
        await queue_manager.store_response(request_id, response)

        logger.info(f"成功完成請求 {request_id} (使用: {current_provider})")

    except Exception as e:
        logger.error(f"處理請求 {request_id} 時發生錯誤 (使用: {current_provider}): {e}")

        # 報告失敗給故障切換管理器
        await failover_manager.report_failure(current_provider)

        # 如果還有備用提供者可以嘗試，進行重試
        if retry_count < max_retries:
            retry_count += 1
            request_item["retry_count"] = retry_count
            request_item["tried_providers"] = tried_providers

            # 獲取所有可用提供者
            all_providers = failover_manager._all_providers()
            available_providers = [
                p for p in all_providers
                if p not in tried_providers and failover_manager.provider_statuses[p]["available"]
            ]

            if available_providers:
                logger.info(f"嘗試使用其他提供者重新處理請求 {request_id}，"
                            f"已嘗試: {tried_providers}，"
                            f"可用: {available_providers}")

                # 延遲一小段時間，避免立即重試
                await asyncio.sleep(1)

                # 重新加入佇列 (優先處理)
                await queue_manager.priority_enqueue(request_item)
                return

        # 所有提供者都嘗試過或達到最大重試次數，儲存錯誤回應
        error_response = {
            "error": {
                "message": f"所有可用 LLM 服務均失敗: {str(e)}",
                "type": "llm_service_error",
                "tried_providers": tried_providers
            },
            "status": "error"
        }
        await queue_manager.store_response(request_id, error_response)


async def process_queue() -> None:
    """背景處理佇列中的請求"""
    logger.info("啟動佇列處理器")

    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            # 檢查是否可以發送請求 (速率限制)，添加超時
            try:
                acquired = await asyncio.wait_for(rate_limiter.wait_for_token(), timeout=2.0)
                if not acquired:
                    # 如果獲取令牌失敗，短暫等待
                    await asyncio.sleep(0.2)
                    continue
            except asyncio.TimeoutError:
                logger.warning("等待速率限制令牌超時，跳過此循環")
                await asyncio.sleep(0.5)
                continue

            # 從佇列中獲取請求，添加超時
            try:
                request_item = await asyncio.wait_for(queue_manager.dequeue(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("從佇列獲取請求超時")
                await asyncio.sleep(0.2)
                continue

            if not request_item:
                # 如果佇列為空，稍微等待
                await asyncio.sleep(0.1)
                consecutive_errors = 0  # 重置錯誤計數
                continue

            # 處理請求 (不等待完成)，但設置超時
            task = asyncio.create_task(process_queue_item_with_timeout(request_item))
            consecutive_errors = 0  # 重置錯誤計數

        except Exception as e:
            consecutive_errors += 1
            logger.error(f"佇列處理器發生錯誤 ({consecutive_errors}/{max_consecutive_errors}): {e}")

            # 如果連續錯誤過多，重新啟動處理器
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"佇列處理器連續失敗 {consecutive_errors} 次，重新初始化...")
                await asyncio.sleep(5)
                consecutive_errors = 0

            # 發生錯誤時稍微等待，避免快速循環消耗資源
            await asyncio.sleep(1)


async def process_queue_item_with_timeout(request_item: Dict[str, Any]) -> None:
    """帶超時的請求處理包裝函數"""
    try:
        # 使用超時機制包裝原始處理函數
        await asyncio.wait_for(process_queue_item(request_item), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error(f"處理請求 {request_item.get('id')} 超時 (超過 30 秒)")

        # 記錄超時錯誤到佇列
        error_response = {"error": {"message": "請求處理超時", "type": "timeout_error"}, "status": "error"}
        try:
            await queue_manager.store_response(request_item.get('id'), error_response)
        except Exception as e:
            logger.error(f"存儲超時錯誤回應失敗: {e}")
    except Exception as e:
        logger.error(f"處理請求包裝函數發生錯誤: {e}")


async def start_queue_processor() -> None:
    """啟動佇列處理器"""
    asyncio.create_task(process_queue())
    logger.info("已啟動佇列處理器")
