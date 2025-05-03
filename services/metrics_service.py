# services/metrics_service.py
import time
import asyncio
from typing import Dict, Any, List, Optional, Counter
import json
from datetime import datetime, timedelta
from collections import defaultdict, deque

from core.setting import settings
from core.logger import logger

class MetricsService:
    """提供者指標追蹤服務，記錄各個 LLM 提供者的使用情況和成功率"""
    
    _instance = None
    
    def __new__(cls):
        """單例模式實作"""
        if cls._instance is None:
            cls._instance = super(MetricsService, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """初始化指標服務"""
        # 設定資料過期時間
        self.metrics_window = settings.METRICS_WINDOW_HOURS * 3600  # 轉換為秒
        
        # 每個提供者的請求記錄
        self.request_logs = defaultdict(list)  # provider -> [request_logs]
        
        # 每個提供者的成功/失敗計數
        self.success_counts = defaultdict(int)  # provider -> success_count
        self.failure_counts = defaultdict(int)  # provider -> failure_count
        
        # 每個提供者的平均回應時間
        self.response_times = defaultdict(list)  # provider -> [response_times]
        
        # 每個提供者的平均 token 使用量
        self.prompt_tokens = defaultdict(list)  # provider -> [prompt_tokens]
        self.completion_tokens = defaultdict(list)  # provider -> [completion_tokens]
        
        # 每個提供者的累計費用
        self.costs = defaultdict(float)  # provider -> total_cost
        
        # 使用滑動窗口來追蹤最近的請求
        self.recent_requests = deque(maxlen=1000)  # 最近 1000 個請求的記錄
        
        # 設定週期性任務以清理過期資料
        self.lock = asyncio.Lock()
        self.cleaner_task = None
        self.running = False
        
        logger.info(f"指標服務初始化完成，指標保留時間窗口：{settings.METRICS_WINDOW_HOURS} 小時")
    
    async def start(self):
        """啟動指標服務"""
        if self.running:
            return
        
        self.running = True
        self.cleaner_task = asyncio.create_task(self._clean_old_data())
        logger.info("指標服務已啟動")
    
    async def stop(self):
        """停止指標服務"""
        if not self.running:
            return
        
        self.running = False
        if self.cleaner_task:
            self.cleaner_task.cancel()
            try:
                await self.cleaner_task
            except asyncio.CancelledError:
                pass
        logger.info("指標服務已停止")
    
    async def _clean_old_data(self):
        """定期清理過期資料"""
        logger.info("開始定期清理過期指標資料")
        
        while self.running:
            try:
                await asyncio.sleep(3600)  # 每小時執行一次
                
                async with self.lock:
                    now = time.time()
                    cutoff = now - self.metrics_window
                    
                    # 清理每個提供者的記錄
                    for provider in list(self.request_logs.keys()):
                        self.request_logs[provider] = [
                            log for log in self.request_logs[provider]
                            if log["timestamp"] > cutoff
                        ]
                        
                        self.response_times[provider] = [
                            (ts, rt) for ts, rt in self.response_times[provider]
                            if ts > cutoff
                        ]
                        
                        self.prompt_tokens[provider] = [
                            (ts, tokens) for ts, tokens in self.prompt_tokens[provider]
                            if ts > cutoff
                        ]
                        
                        self.completion_tokens[provider] = [
                            (ts, tokens) for ts, tokens in self.completion_tokens[provider]
                            if ts > cutoff
                        ]
                    
                    # 清理成功/失敗計數（如果對應的提供者沒有最近的記錄）
                    for provider in list(self.success_counts.keys()):
                        if provider not in self.request_logs or not self.request_logs[provider]:
                            del self.success_counts[provider]
                    
                    for provider in list(self.failure_counts.keys()):
                        if provider not in self.request_logs or not self.request_logs[provider]:
                            del self.failure_counts[provider]
                    
                    # 清理滑動窗口
                    while self.recent_requests and self.recent_requests[0]["timestamp"] < cutoff:
                        self.recent_requests.popleft()
                
                logger.debug("已清理過期指標資料")
                
            except asyncio.CancelledError:
                logger.info("指標資料清理任務被取消")
                break
            except Exception as e:
                logger.error(f"清理指標資料時發生錯誤: {e}")
                await asyncio.sleep(60)  # 發生錯誤時等待較短時間
    
    async def record_request(self, provider: str, request_id: str, request_data: Dict[str, Any]):
        """
        記錄 API 請求
        
        Args:
            provider: 提供者名稱
            request_id: 請求 ID
            request_data: 請求資料
        """
        async with self.lock:
            # 記錄基本請求資訊
            timestamp = time.time()
            
            # 提取請求特徵（用於分析）
            try:
                messages_count = len(request_data.get("messages", []))
                model = request_data.get("model", "unknown")
                
                log_entry = {
                    "request_id": request_id,
                    "provider": provider,
                    "timestamp": timestamp,
                    "datetime": datetime.fromtimestamp(timestamp).isoformat(),
                    "model": model,
                    "messages_count": messages_count,
                    "completed": False,
                    "success": None,
                    "duration": None,
                }
                
                # 保存到對應提供者的記錄中
                self.request_logs[provider].append(log_entry)
                
                # 添加到最近請求隊列
                self.recent_requests.append(log_entry)
                
                logger.debug(f"記錄請求：{provider}, {request_id}")
            except Exception as e:
                logger.error(f"記錄請求時發生錯誤: {e}")
    
    async def record_response(
        self, 
        provider: str, 
        request_id: str, 
        success: bool, 
        duration: float,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        cost: Optional[float] = None
    ):
        """
        記錄 API 回應
        
        Args:
            provider: 提供者名稱
            request_id: 請求 ID
            success: 是否成功
            duration: 處理時間（秒）
            prompt_tokens: 提示 token 數量
            completion_tokens: 完成 token 數量
            cost: 請求成本
        """
        async with self.lock:
            try:
                timestamp = time.time()
                
                # 更新對應請求的記錄
                for log in self.request_logs[provider]:
                    if log["request_id"] == request_id and not log["completed"]:
                        log["completed"] = True
                        log["success"] = success
                        log["duration"] = duration
                        log["prompt_tokens"] = prompt_tokens
                        log["completion_tokens"] = completion_tokens
                        log["cost"] = cost
                        break
                
                # 更新最近請求隊列中的記錄
                for log in self.recent_requests:
                    if log["request_id"] == request_id and not log["completed"]:
                        log["completed"] = True
                        log["success"] = success
                        log["duration"] = duration
                        log["prompt_tokens"] = prompt_tokens
                        log["completion_tokens"] = completion_tokens
                        log["cost"] = cost
                        break
                
                # 更新成功/失敗計數
                if success:
                    self.success_counts[provider] += 1
                else:
                    self.failure_counts[provider] += 1
                
                # 更新回應時間記錄
                self.response_times[provider].append((timestamp, duration))
                
                # 更新 token 使用量記錄
                if prompt_tokens is not None:
                    self.prompt_tokens[provider].append((timestamp, prompt_tokens))
                
                if completion_tokens is not None:
                    self.completion_tokens[provider].append((timestamp, completion_tokens))
                
                # 更新費用記錄
                if cost is not None:
                    self.costs[provider] += cost
                
                logger.debug(f"記錄回應：{provider}, {request_id}, 成功: {success}, 時間: {duration:.2f}秒")
            except Exception as e:
                logger.error(f"記錄回應時發生錯誤: {e}")
    
    def get_metrics(self, provider: Optional[str] = None, time_window: Optional[int] = None) -> Dict[str, Any]:
        """
        獲取指標資料
        
        Args:
            provider: 指定提供者（如果為 None，則返回所有提供者的彙總指標）
            time_window: 時間窗口（秒），如果指定，則只返回該窗口內的指標
            
        Returns:
            Dict[str, Any]: 指標資料
        """
        now = time.time()
        
        # 預設使用完整的指標窗口
        if time_window is None:
            time_window = self.metrics_window
        
        cutoff = now - time_window
        
        if provider:
            # 返回指定提供者的指標
            return self._get_provider_metrics(provider, cutoff)
        else:
            # 返回所有提供者的彙總指標
            providers = list(self.request_logs.keys())
            
            all_metrics = {
                "overall": self._get_overall_metrics(cutoff),
                "providers": {p: self._get_provider_metrics(p, cutoff) for p in providers}
            }
            
            return all_metrics
    
    def _get_provider_metrics(self, provider: str, cutoff: float) -> Dict[str, Any]:
        """
        獲取指定提供者的指標
        
        Args:
            provider: 提供者名稱
            cutoff: 時間截點
            
        Returns:
            Dict[str, Any]: 提供者指標
        """
        # 只考慮時間窗口內的記錄
        logs = [log for log in self.request_logs.get(provider, []) if log["timestamp"] > cutoff]
        
        # 計算成功率
        total_completed = sum(1 for log in logs if log["completed"])
        success_count = sum(1 for log in logs if log.get("success", False))
        
        success_rate = (success_count / total_completed * 100) if total_completed > 0 else 0
        
        # 計算平均回應時間
        response_times = [log["duration"] for log in logs if log.get("duration") is not None]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        # 計算 token 使用量
        prompt_tokens = [log.get("prompt_tokens", 0) for log in logs if log.get("prompt_tokens") is not None]
        completion_tokens = [log.get("completion_tokens", 0) for log in logs if log.get("completion_tokens") is not None]
        
        total_prompt_tokens = sum(prompt_tokens)
        total_completion_tokens = sum(completion_tokens)
        
        # 計算費用
        costs = [log.get("cost", 0) for log in logs if log.get("cost") is not None]
        total_cost = sum(costs)
        
        # 獲取提供者的模型使用情況
        model_usage = defaultdict(int)
        for log in logs:
            if "model" in log:
                model_usage[log["model"]] += 1
        
        # 請求量隨時間變化
        hourly_requests = defaultdict(int)
        for log in logs:
            hour = datetime.fromtimestamp(log["timestamp"]).strftime("%Y-%m-%d %H:00")
            hourly_requests[hour] += 1
        
        return {
            "request_count": len(logs),
            "completed_count": total_completed,
            "success_count": success_count,
            "failure_count": total_completed - success_count,
            "success_rate": round(success_rate, 2),
            "avg_response_time": round(avg_response_time, 2),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_cost": round(total_cost, 4),
            "model_usage": dict(model_usage),
            "hourly_requests": dict(sorted(hourly_requests.items())),
        }
    
    def _get_overall_metrics(self, cutoff: float) -> Dict[str, Any]:
        """
        獲取整體指標
        
        Args:
            cutoff: 時間截點
            
        Returns:
            Dict[str, Any]: 整體指標
        """
        # 彙總所有提供者的記錄
        all_logs = []
        for provider in self.request_logs:
            all_logs.extend([log for log in self.request_logs[provider] if log["timestamp"] > cutoff])
        
        # 計算成功率
        total_completed = sum(1 for log in all_logs if log["completed"])
        success_count = sum(1 for log in all_logs if log.get("success", False))
        
        success_rate = (success_count / total_completed * 100) if total_completed > 0 else 0
        
        # 計算平均回應時間
        response_times = [log["duration"] for log in all_logs if log.get("duration") is not None]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        # 計算 token 使用量
        prompt_tokens = [log.get("prompt_tokens", 0) for log in all_logs if log.get("prompt_tokens") is not None]
        completion_tokens = [log.get("completion_tokens", 0) for log in all_logs if log.get("completion_tokens") is not None]
        
        total_prompt_tokens = sum(prompt_tokens)
        total_completion_tokens = sum(completion_tokens)
        
        # 計算總費用
        total_cost = sum(self.costs.values())
        
        # 提供者使用情況
        provider_usage = defaultdict(int)
        for log in all_logs:
            provider_usage[log["provider"]] += 1
        
        # 請求量隨時間變化
        hourly_requests = defaultdict(int)
        for log in all_logs:
            hour = datetime.fromtimestamp(log["timestamp"]).strftime("%Y-%m-%d %H:00")
            hourly_requests[hour] += 1
        
        # 故障切換發生次數
        failover_events = []
        last_provider = None
        
        for log in sorted(all_logs, key=lambda x: x["timestamp"]):
            current_provider = log["provider"]
            if last_provider and current_provider != last_provider:
                failover_events.append({
                    "timestamp": log["timestamp"],
                    "datetime": datetime.fromtimestamp(log["timestamp"]).isoformat(),
                    "from": last_provider,
                    "to": current_provider
                })
            last_provider = current_provider
        
        return {
            "request_count": len(all_logs),
            "completed_count": total_completed,
            "success_count": success_count,
            "failure_count": total_completed - success_count,
            "success_rate": round(success_rate, 2),
            "avg_response_time": round(avg_response_time, 2),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_cost": round(total_cost, 4),
            "provider_usage": dict(provider_usage),
            "hourly_requests": dict(sorted(hourly_requests.items())),
            "failover_events": failover_events,
            "failover_count": len(failover_events)
        }

# 單例訪問函數
def get_metrics_service() -> MetricsService:
    """取得指標服務實例"""
    return MetricsService()