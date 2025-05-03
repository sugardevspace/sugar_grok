from fastapi import APIRouter, Depends, HTTPException
import json
from typing import Optional

from core.auth import authenticate
from core.logger import logger
from core.setting import settings
from models.model import (ChatRequest, QueuedRequestResponse, StatsResponse, APIUsageStats, SystemStatus)
from services.queue.factory import get_queue_manager
from services.llm.factory import get_llm_stats, get_all_providers
from utils.api_key_manager import get_key_manager
from services.failover_manager import get_failover_manager
from services.metrics_service import get_metrics_service

router = APIRouter()
queue_manager = get_queue_manager()
key_manager = get_key_manager()
failover_manager = get_failover_manager()


@router.post("/chat/completions")
async def chat_completions(request: ChatRequest, _: str = Depends(authenticate)):
    """將聊天請求排入佇列並立即返回請求 ID"""
    logger.info(f"接收到聊天請求，模型: {request.model}, 訊息數量: {len(request.messages)}")

    # 加入 queue 中等待執行
    request_id = await queue_manager.enqueue(request.model_dump())
    queue_length = await queue_manager.get_queue_length()

    # 估計處理時間
    estimated_seconds = max(1, queue_length // settings.RATE_LIMIT_RPS)

    return QueuedRequestResponse(request_id=request_id,
                                 queue_position=queue_length,
                                 estimated_time=f"{estimated_seconds} 秒")


@router.get("/requests/{request_id}")
async def get_request_status(request_id: str, _: str = Depends(authenticate)):
    """檢查特定請求的狀態和結果"""
    # 嘗試從佇列中獲取回應
    response_data = await queue_manager.get_response(request_id)

    if not response_data:
        return {"request_id": request_id, "status": "pending", "message": "請求正在處理中或不存在"}

    return json.loads(response_data)


@router.get("/stats", response_model=StatsResponse)
async def get_api_stats(provider: Optional[str] = None, _: str = Depends(authenticate)):
    """
    獲取 API 使用統計

    Args:
        provider: 可選，指定要獲取統計的提供者
    """
    # 從 LLM 服務獲取統計
    llm_stats = get_llm_stats(provider)  # 修改 get_llm_stats 以支持可選提供者

    # 獲取佇列長度
    queue_length = await queue_manager.get_queue_length()

    # 獲取 API 金鑰統計
    # 如果提供了特定提供者，只獲取該提供者的金鑰統計
    key_stats = key_manager.get_key_stats(provider)

    # 轉換為 API 回應格式
    usage_stats = APIUsageStats(total_requests=llm_stats.get("total_requests", 0),
                                total_prompt_tokens=llm_stats.get("total_prompt_tokens", 0),
                                total_completion_tokens=llm_stats.get("total_completion_tokens", 0),
                                total_cost=llm_stats.get("total_cost", 0.0),
                                requests_per_second=llm_stats.get("requests_per_second", 0.0),
                                error_429_count=llm_stats.get("error_429_count", 0),
                                other_errors_count=llm_stats.get("other_errors_count", 0))

    return StatsResponse(usage_stats=usage_stats, current_queue_length=queue_length, api_keys=key_stats)


@router.get("/system/status")
async def get_system_status(provider: Optional[str] = None, _: str = Depends(authenticate)):
    """獲取系統狀態，包括故障切換狀態"""
    # 獲取佇列長度
    queue_length = await queue_manager.get_queue_length()

    # 獲取 LLM 統計
    # 如果提供了特定提供者，只獲取該提供者的統計
    llm_stats = get_llm_stats(provider)

    # 獲取故障切換狀態
    failover_status = failover_manager.get_status()

    # 獲取指標資料
    metrics = None
    if settings.ENABLE_METRICS:
        try:
            metrics_service = get_metrics_service()
            # 如果提供了特定提供者，只獲取該提供者的指標
            metrics = metrics_service.get_metrics(
                provider=provider,
                time_window=3600  # 最近一小時的指標
            )
        except Exception as e:
            logger.error(f"獲取指標資料失敗: {e}")

    return SystemStatus(queue_status={"current_length": queue_length},
                        llm_stats=llm_stats,
                        failover_status=failover_status,
                        metrics=metrics)


# 其他方法保持不變


@router.post("/system/force-failover/{provider}")
async def force_failover(provider: str, _: str = Depends(authenticate)):
    """強制切換到指定的提供者"""
    try:
        all_providers = failover_manager._all_providers()

        if provider not in all_providers:
            raise HTTPException(status_code=400, detail=f"無效的提供者: {provider}。有效選項: {all_providers}")

        # 暫時設定為指定提供者
        prev_provider = failover_manager.current_provider
        failover_manager.current_provider = provider

        # 如果切換到主要提供者，退出故障切換模式
        if provider == failover_manager.primary_provider:
            failover_manager.in_failover_mode = False
        else:
            failover_manager.in_failover_mode = True

        logger.warning(f"手動強制故障切換: 從 {prev_provider} 切換到 {provider}")

        return {
            "success": True,
            "message": f"成功切換到 {provider}",
            "previous_provider": prev_provider,
            "current_provider": provider
        }
    except Exception as e:
        logger.error(f"強制故障切換失敗: {e}")
        raise HTTPException(status_code=500, detail=f"切換失敗: {str(e)}")


@router.post("/system/reset-provider/{provider}")
async def reset_provider_status(provider: str, _: str = Depends(authenticate)):
    """重設指定提供者的狀態"""
    try:
        all_providers = failover_manager._all_providers()

        if provider not in all_providers:
            raise HTTPException(status_code=400, detail=f"無效的提供者: {provider}。有效選項: {all_providers}")

        # 重設提供者狀態
        failover_manager.provider_statuses[provider]["available"] = True
        failover_manager.failure_counts[provider] = 0

        logger.info(f"手動重設提供者狀態: {provider}")

        return {
            "success": True,
            "message": f"成功重設 {provider} 的狀態",
            "provider_status": failover_manager.provider_statuses[provider]
        }
    except Exception as e:
        logger.error(f"重設提供者狀態失敗: {e}")
        raise HTTPException(status_code=500, detail=f"重設失敗: {str(e)}")


@router.get("/providers")
async def get_providers(_: str = Depends(authenticate)):
    """獲取所有支援的提供者列表"""
    providers = get_all_providers()

    return {
        "providers": providers,
        "current_provider": failover_manager.current_provider,
        "primary_provider": failover_manager.primary_provider
    }
