from typing import List, Dict, Optional, Any
from pydantic import BaseModel

from core.setting import settings


class Message(BaseModel):
    """聊天訊息模型"""
    role: str
    content: str


class ChatRequest(BaseModel):
    """聊天完成請求模型"""
    model: str = settings.DEFAULT_MODEL
    messages: List[Message]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 10000
    top_p: Optional[float] = 0.95
    response_format: Optional[str] = "chat"  # 新增回應格式選項


class QueuedRequestResponse(BaseModel):
    """佇列請求回應模型"""
    request_id: str
    status: str = "queued"
    queue_position: int
    estimated_time: str


class TokenCount(BaseModel):
    """Token 計數模型"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class APIKeyStats(BaseModel):
    """API 金鑰統計"""
    usage_count: int
    last_used: str


class APIUsageStats(BaseModel):
    """API 使用統計"""
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost: float = 0.0
    requests_per_second: float = 0.0
    error_429_count: int = 0
    other_errors_count: int = 0


class StatsResponse(BaseModel):
    """統計回應模型"""
    usage_stats: APIUsageStats
    current_queue_length: int
    api_keys: Dict[str, APIKeyStats]


class ProviderStatus(BaseModel):
    """提供者狀態"""
    available: bool
    failure_count: int
    last_check: str


class FailoverStatus(BaseModel):
    """故障切換狀態"""
    current_provider: str
    primary_provider: str
    failover_providers: List[str]
    in_failover_mode: bool
    provider_statuses: Dict[str, ProviderStatus]


class FailoverResponse(BaseModel):
    """故障切換回應"""
    success: bool
    message: str
    previous_provider: str
    current_provider: str


class SystemStatus(BaseModel):
    """系統狀態回應"""
    queue_status: Dict[str, Any]
    llm_stats: Dict[str, Any]
    failover_status: FailoverStatus
    metrics: Optional[Dict[str, Any]] = None
