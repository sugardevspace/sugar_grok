#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API服務器併發穩定性測試
此測試檔案用於測試 API 服務器在高負載和併發情況下的穩定性
"""

import asyncio
import aiohttp
import time
import random
import argparse
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# 配置日誌
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(f"concurrency_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger('concurrency_test')

# 測試配置
DEFAULT_CONFIG = {
    "base_url": "http://localhost:8000/v1",  # API 基礎 URL
    "api_key": "your_api_key",  # 替換為您的 API 金鑰
    "concurrency": 10,  # 併發請求數
    "request_count": 100,  # 總請求數
    "delay_range": (0.1, 0.5),  # 請求間隔隨機延遲範圍（秒）
    "test_duration": 60,  # 測試持續時間（秒），如果設置為0則使用請求數來決定測試長度
    "check_interval": 0.5,  # 檢查請求結果的間隔（秒）
    "timeout": 30,  # 請求超時（秒）
    "random_models": True,  # 是否隨機使用不同模型
    "models": ["grok-3", "grok-3-mini", "gpt-4o"],  # 可用模型列表
    "random_response_formats": True,  # 是否隨機使用不同回應格式
    "response_formats": ["chat", "story", "stimulation", "intimacy", "level"],  # 可用回應格式
    "test_force_failover": True,  # 是否測試強制故障切換
    "test_api_stats": True,  # 是否測試 API 統計端點
    "test_system_status": True,  # 是否測試系統狀態端點
    "test_reset_provider": True,  # 是否測試重設提供者狀態
}


class ConcurrencyTest:
    """API 服務器併發測試類"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化測試
        
        Args:
            config: 測試配置
        """
        self.config = config
        self.base_url = config["base_url"]
        self.api_key = config["api_key"]
        self.concurrency = config["concurrency"]
        self.request_count = config["request_count"]
        self.delay_range = config["delay_range"]
        self.test_duration = config["test_duration"]
        self.check_interval = config["check_interval"]
        self.timeout = config["timeout"]
        self.models = config["models"]
        self.response_formats = config["response_formats"]

        # 追蹤進行中的請求
        self.active_requests = {}
        self.completed_requests = []
        self.failed_requests = []
        self.pending_requests = []

        # 性能統計
        self.stats = {
            "start_time": None,
            "end_time": None,
            "request_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "timeout_count": 0,
            "response_times": [],
            "queue_times": [],
            "total_execution_time": 0,
            "average_response_time": 0,
            "min_response_time": float('inf'),
            "max_response_time": 0,
            "requests_per_second": 0,
        }

        # 併發限制
        self.semaphore = asyncio.Semaphore(self.concurrency)

        # 測試運行控制
        self.running = False

        logger.info(f"測試配置: {json.dumps(config, indent=2, ensure_ascii=False)}")

    async def run_test(self):
        """運行完整測試"""
        self.running = True
        self.stats["start_time"] = time.time()

        logger.info(f"開始測試 - 併發度: {self.concurrency}, 請求數: {self.request_count}")

        # 使用時間或請求數作為終止條件
        if self.test_duration > 0:
            # 基於時間的測試
            end_time = time.time() + self.test_duration
            request_task = asyncio.create_task(self._generate_requests_by_time(end_time))
        else:
            # 基於請求數的測試
            request_task = asyncio.create_task(self._generate_requests_by_count(self.request_count))

        # 啟動結果檢查任務
        check_task = asyncio.create_task(self._check_results())

        # 如果設置了測試故障切換，則在測試中間觸發故障切換
        if self.config["test_force_failover"]:
            # 在測試運行一半時執行故障切換
            if self.test_duration > 0:
                failover_delay = self.test_duration / 2
            else:
                failover_delay = 5  # 5秒後觸發故障切換

            asyncio.create_task(self._trigger_failover_after_delay(failover_delay))

        # 如果設置了測試 API 統計，定期檢查 API 統計
        if self.config["test_api_stats"]:
            asyncio.create_task(self._check_api_stats_periodically())

        # 如果設置了測試系統狀態，定期檢查系統狀態
        if self.config["test_system_status"]:
            asyncio.create_task(self._check_system_status_periodically())

        # 等待請求生成任務完成
        await request_task

        # 等待所有請求完成處理或超時
        if self.active_requests:
            logger.info(f"等待 {len(self.active_requests)} 個進行中的請求完成...")
            try:
                await asyncio.wait_for(self._wait_for_all_requests(), timeout=self.timeout + 10)
            except asyncio.TimeoutError:
                logger.warning(f"等待請求完成超時，仍有 {len(self.active_requests)} 個請求未完成")

        # 停止結果檢查任務
        self.running = False
        await check_task

        # 記錄統計資料
        self.stats["end_time"] = time.time()
        self.stats["total_execution_time"] = self.stats["end_time"] - self.stats["start_time"]
        if self.stats["success_count"] > 0:
            self.stats["average_response_time"] = sum(self.stats["response_times"]) / self.stats["success_count"]
        if self.stats["total_execution_time"] > 0:
            self.stats["requests_per_second"] = self.stats["request_count"] / self.stats["total_execution_time"]

        # 輸出測試結果
        self._print_test_results()

        # 返回測試統計
        return self.stats

    async def _generate_requests_by_count(self, count: int):
        """
        生成指定數量的請求
        
        Args:
            count: 請求數量
        """
        tasks = []
        for i in range(count):
            # 隨機延遲，避免同時發送所有請求
            if i > 0:
                delay = random.uniform(*self.delay_range)
                await asyncio.sleep(delay)

            if not self.running:
                break

            task = asyncio.create_task(self._send_chat_request(i))
            tasks.append(task)

            # 更新統計
            self.stats["request_count"] += 1

        # 等待所有請求任務完成
        if tasks:
            await asyncio.gather(*tasks)

    async def _generate_requests_by_time(self, end_time: float):
        """
        在指定時間內生成請求
        
        Args:
            end_time: 結束時間戳
        """
        request_id = 0
        while time.time() < end_time and self.running:
            # 隨機延遲，控制請求速率
            delay = random.uniform(*self.delay_range)
            await asyncio.sleep(delay)

            task = asyncio.create_task(self._send_chat_request(request_id))
            request_id += 1

            # 更新統計
            self.stats["request_count"] += 1

    async def _send_chat_request(self, request_id: int):
        """
        發送聊天請求並追蹤
        
        Args:
            request_id: 請求 ID
        """
        # 使用信號量限制併發請求數
        async with self.semaphore:
            try:
                # 選擇一個模型和回應格式
                model = random.choice(self.models) if self.config["random_models"] else self.models[0]
                response_format = random.choice(
                    self.response_formats) if self.config["random_response_formats"] else "chat"

                # 建立聊天請求
                request_data = {
                    "model": model,
                    "response_format": response_format,
                    "messages": [{
                        "role": "user",
                        "content": f"測試請求 {request_id}. 請回答：你是什麼模型？今天的日期是？"
                    }],
                    "temperature": 0.7
                }

                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

                start_time = time.time()

                # 發送請求
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{self.base_url}/chat/completions",
                                            json=request_data,
                                            headers=headers,
                                            timeout=self.timeout) as response:
                        response_time = time.time() - start_time

                        # 處理回應
                        if response.status == 200:
                            response_data = await response.json()
                            queue_request_id = response_data.get("request_id")

                            # 記錄請求資訊
                            self.active_requests[queue_request_id] = {
                                "request_id": request_id,
                                "queue_request_id": queue_request_id,
                                "start_time": start_time,
                                "queue_time": response_time,
                                "model": model,
                                "response_format": response_format,
                                "status": "queued",
                                "check_count": 0
                            }

                            self.pending_requests.append(queue_request_id)
                            logger.debug(
                                f"請求 {request_id} 已排入佇列，佇列 ID: {queue_request_id}, 位置: {response_data.get('queue_position', 'unknown')}"
                            )
                        else:
                            error_text = await response.text()
                            logger.error(f"請求 {request_id} 失敗: HTTP {response.status} - {error_text}")

                            # 記錄失敗
                            self.failed_requests.append({
                                "request_id": request_id,
                                "error": f"HTTP {response.status}",
                                "details": error_text,
                                "time": time.time() - start_time
                            })

                            # 更新統計
                            self.stats["failure_count"] += 1

            except asyncio.TimeoutError:
                logger.error(f"請求 {request_id} 超時")

                # 記錄超時
                self.failed_requests.append({
                    "request_id": request_id,
                    "error": "Timeout",
                    "time": time.time() - start_time
                })

                # 更新統計
                self.stats["timeout_count"] += 1
                self.stats["failure_count"] += 1

            except Exception as e:
                logger.error(f"請求 {request_id} 發生錯誤: {str(e)}")

                # 記錄錯誤
                self.failed_requests.append({
                    "request_id": request_id,
                    "error": str(e),
                    "time": time.time() - start_time
                })

                # 更新統計
                self.stats["failure_count"] += 1

    async def _check_results(self):
        """檢查已排入佇列的請求結果"""
        while self.running or self.active_requests:
            # 檢查每個進行中的請求
            requests_to_check = list(self.pending_requests)
            for queue_request_id in requests_to_check:
                if queue_request_id not in self.active_requests:
                    self.pending_requests.remove(queue_request_id)
                    continue

                request_info = self.active_requests[queue_request_id]
                request_info["check_count"] += 1

                try:
                    # 檢查請求狀態
                    async with aiohttp.ClientSession() as session:
                        headers = {"Authorization": f"Bearer {self.api_key}"}
                        async with session.get(f"{self.base_url}/requests/{queue_request_id}",
                                               headers=headers,
                                               timeout=self.timeout) as response:
                            if response.status == 200:
                                response_data = await response.json()
                                status = response_data.get("status")

                                if status == "completed":
                                    # 請求已完成
                                    end_time = time.time()
                                    total_time = end_time - request_info["start_time"]
                                    processing_time = total_time - request_info["queue_time"]

                                    # 記錄完成
                                    self.completed_requests.append({
                                        "request_id": request_info["request_id"],
                                        "queue_request_id": queue_request_id,
                                        "total_time": total_time,
                                        "queue_time": request_info["queue_time"],
                                        "processing_time": processing_time,
                                        "model": request_info["model"],
                                        "response_format": request_info["response_format"],
                                        "check_count": request_info["check_count"]
                                    })

                                    # 更新統計
                                    self.stats["success_count"] += 1
                                    self.stats["response_times"].append(total_time)
                                    self.stats["queue_times"].append(request_info["queue_time"])

                                    if total_time < self.stats["min_response_time"]:
                                        self.stats["min_response_time"] = total_time
                                    if total_time > self.stats["max_response_time"]:
                                        self.stats["max_response_time"] = total_time

                                    # 從活動請求中移除
                                    self.pending_requests.remove(queue_request_id)
                                    del self.active_requests[queue_request_id]

                                    logger.debug(f"請求 {request_info['request_id']} 已完成，總時間: {total_time:.2f}秒")

                                elif status == "error":
                                    # 請求失敗
                                    error_msg = response_data.get("error", {}).get("message", "Unknown error")

                                    # 記錄失敗
                                    self.failed_requests.append({
                                        "request_id": request_info["request_id"],
                                        "queue_request_id": queue_request_id,
                                        "error": error_msg,
                                        "time": time.time() - request_info["start_time"]
                                    })

                                    # 更新統計
                                    self.stats["failure_count"] += 1

                                    # 從活動請求中移除
                                    self.pending_requests.remove(queue_request_id)
                                    del self.active_requests[queue_request_id]

                                    logger.warning(f"請求 {request_info['request_id']} 失敗: {error_msg}")

                                # 如果仍在等待，則繼續保持在待檢查列表中

                            else:
                                error_text = await response.text()
                                logger.warning(f"檢查請求 {queue_request_id} 狀態失敗: HTTP {response.status} - {error_text}")

                except Exception as e:
                    logger.warning(f"檢查請求 {queue_request_id} 狀態時發生錯誤: {str(e)}")

                # 檢查是否請求已超時
                if time.time() - request_info["start_time"] > self.timeout:
                    # 記錄超時
                    self.failed_requests.append({
                        "request_id": request_info["request_id"],
                        "queue_request_id": queue_request_id,
                        "error": "Request timeout after queued",
                        "time": time.time() - request_info["start_time"]
                    })

                    # 更新統計
                    self.stats["timeout_count"] += 1
                    self.stats["failure_count"] += 1

                    # 從活動請求中移除
                    self.pending_requests.remove(queue_request_id)
                    del self.active_requests[queue_request_id]

                    logger.warning(f"請求 {request_info['request_id']} 在佇列中超時")

            # 等待一段時間再進行下一輪檢查
            await asyncio.sleep(self.check_interval)

    async def _wait_for_all_requests(self):
        """等待所有活動請求完成"""
        while self.active_requests:
            await asyncio.sleep(0.5)

    async def _trigger_failover_after_delay(self, delay: float):
        """
        在延遲後觸發故障切換
        
        Args:
            delay: 延遲時間（秒）
        """
        await asyncio.sleep(delay)

        # 獲取可用提供者列表
        providers = await self._get_providers()

        if not providers or len(providers.get("providers", [])) < 2:
            logger.warning("無法進行故障切換測試：提供者列表為空或只有一個提供者")
            return

        current_provider = providers.get("current_provider")
        all_providers = providers.get("providers", [])

        # 選擇一個不同的提供者
        target_providers = [p for p in all_providers if p != current_provider]
        if not target_providers:
            logger.warning(f"無法進行故障切換測試：找不到與當前提供者 {current_provider} 不同的提供者")
            return

        target_provider = random.choice(target_providers)

        # 執行故障切換
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/system/force-failover/{target_provider}",
                                        headers=headers,
                                        timeout=10) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        logger.info(f"已觸發故障切換: {response_data}")
                    else:
                        error_text = await response.text()
                        logger.warning(f"故障切換失敗: HTTP {response.status} - {error_text}")

        except Exception as e:
            logger.warning(f"執行故障切換時發生錯誤: {str(e)}")

        # 如果設置了重設提供者，在故障切換一段時間後重設原始提供者
        if self.config["test_reset_provider"] and current_provider:
            await asyncio.sleep(10)  # 等待 10 秒後重設
            await self._reset_provider(current_provider)

    async def _reset_provider(self, provider: str):
        """
        重設提供者狀態
        
        Args:
            provider: 提供者名稱
        """
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/system/reset-provider/{provider}",
                                        headers=headers,
                                        timeout=10) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        logger.info(f"已重設提供者 {provider}: {response_data}")
                    else:
                        error_text = await response.text()
                        logger.warning(f"重設提供者 {provider} 失敗: HTTP {response.status} - {error_text}")

        except Exception as e:
            logger.warning(f"重設提供者 {provider} 時發生錯誤: {str(e)}")

    async def _get_providers(self) -> Dict[str, Any]:
        """
        獲取提供者列表
        
        Returns:
            Dict[str, Any]: 提供者信息
        """
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/providers", headers=headers, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"獲取提供者列表失敗: HTTP {response.status}")
                        return {}

        except Exception as e:
            logger.warning(f"獲取提供者列表時發生錯誤: {str(e)}")
            return {}

    async def _check_api_stats_periodically(self):
        """定期檢查 API 統計"""
        check_interval = 10  # 每 10 秒檢查一次
        while self.running:
            await self._check_api_stats()
            await asyncio.sleep(check_interval)

    async def _check_api_stats(self):
        """檢查 API 統計"""
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/stats", headers=headers, timeout=10) as response:
                    if response.status == 200:
                        stats_data = await response.json()
                        logger.debug(f"API 統計: {json.dumps(stats_data, ensure_ascii=False)}")
                    else:
                        logger.warning(f"獲取 API 統計失敗: HTTP {response.status}")

        except Exception as e:
            logger.warning(f"獲取 API 統計時發生錯誤: {str(e)}")

    async def _check_system_status_periodically(self):
        """定期檢查系統狀態"""
        check_interval = 15  # 每 15 秒檢查一次
        while self.running:
            await self._check_system_status()
            await asyncio.sleep(check_interval)

    async def _check_system_status(self):
        """檢查系統狀態"""
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/system/status", headers=headers, timeout=10) as response:
                    if response.status == 200:
                        status_data = await response.json()
                        logger.debug(f"系統狀態: {json.dumps(status_data, ensure_ascii=False)}")

                        # 檢查故障切換狀態
                        if "failover_status" in status_data:
                            failover_status = status_data["failover_status"]
                            current_provider = failover_status.get("current_provider")
                            in_failover_mode = failover_status.get("in_failover_mode")

                            if in_failover_mode:
                                logger.info(f"系統當前處於故障切換模式，使用 {current_provider} 提供者")
                    else:
                        logger.warning(f"獲取系統狀態失敗: HTTP {response.status}")

        except Exception as e:
            logger.warning(f"獲取系統狀態時發生錯誤: {str(e)}")

    def _print_test_results(self):
        """輸出測試結果"""
        logger.info("=" * 50)
        logger.info("測試結果摘要")
        logger.info("=" * 50)
        logger.info(f"測試持續時間: {self.stats['total_execution_time']:.2f} 秒")
        logger.info(f"總請求數: {self.stats['request_count']}")
        logger.info(f"成功請求數: {self.stats['success_count']}")
        logger.info(f"失敗請求數: {self.stats['failure_count']}")
        logger.info(f"超時請求數: {self.stats['timeout_count']}")

        if self.stats['success_count'] > 0:
            logger.info(f"平均響應時間: {self.stats['average_response_time']:.2f} 秒")
            logger.info(f"最短響應時間: {self.stats['min_response_time']:.2f} 秒")
            logger.info(f"最長響應時間: {self.stats['max_response_time']:.2f} 秒")

        logger.info(f"每秒處理請求數: {self.stats['requests_per_second']:.2f}")

        # 檢查是否有致命錯誤
        fatal_errors = [req for req in self.failed_requests if "connection" in req.get("error", "").lower()]
        if fatal_errors:
            logger.error(f"發現 {len(fatal_errors)} 個致命連接錯誤，可能表示系統不穩定或當機")

        # 判斷整體穩定性
        if self.stats['failure_count'] == 0:
            logger.info("測試結果: 非常穩定 - 沒有任何失敗")
        elif self.stats['failure_count'] / self.stats['request_count'] < 0.05:
            logger.info("測試結果: 穩定 - 失敗率低於 5%")
        elif self.stats['failure_count'] / self.stats['request_count'] < 0.20:
            logger.info("測試結果: 基本穩定 - 失敗率低於 20%")
        else:
            logger.warning("測試結果: 不穩定 - 失敗率高於 20%")

        logger.info("=" * 50)


async def main():
    """主函數"""
    parser = argparse.ArgumentParser(description="API 服務器併發穩定性測試")
    parser.add_argument("--base-url", help="API 基礎 URL", default=DEFAULT_CONFIG["base_url"])
    parser.add_argument("--api-key", help="API 金鑰", default=DEFAULT_CONFIG["api_key"])
    parser.add_argument("--concurrency", type=int, help="併發請求數", default=DEFAULT_CONFIG["concurrency"])
    parser.add_argument("--requests", type=int, help="總請求數", default=DEFAULT_CONFIG["request_count"])
    parser.add_argument("--duration", type=int, help="測試持續時間（秒）", default=DEFAULT_CONFIG["test_duration"])
    parser.add_argument("--timeout", type=int, help="請求超時（秒）", default=DEFAULT_CONFIG["timeout"])
    parser.add_argument("--models", help="模型列表（逗號分隔）", default=",".join(DEFAULT_CONFIG["models"]))

    args = parser.parse_args()

    # 更新配置
    config = DEFAULT_CONFIG.copy()
    config.update({
        "base_url": args.base_url,
        "api_key": args.api_key,
        "concurrency": args.concurrency,
        "request_count": args.requests,
        "test_duration": args.duration,
        "timeout": args.timeout,
        "models": args.models.split(",")
    })

    # 創建並運行測試
    test = ConcurrencyTest(config)
    stats = await test.run_test()

    # 輸出更詳細的統計
    detailed_stats = {
        "test_configuration": config,
        "performance_summary": stats,
        "completion_time_distribution": _analyze_time_distribution(test.completed_requests),
        "failure_analysis": _analyze_failures(test.failed_requests),
        "stability_score": _calculate_stability_score(stats)
    }

    # 將詳細統計保存到文件
    filename = f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(detailed_stats, f, indent=2, ensure_ascii=False)

    logger.info(f"詳細測試結果已保存到 {filename}")
    logger.info(f"穩定性評分: {detailed_stats['stability_score']}/10")

    # 檢查是否有崩潰或當機的跡象
    if _check_for_crash_indicators(test.failed_requests):
        logger.critical("測試結果表明系統可能存在當機或嚴重穩定性問題")

    return detailed_stats


def _analyze_time_distribution(completed_requests):
    """分析請求完成時間分佈"""
    if not completed_requests:
        return {"empty": True}

    # 提取總時間、排隊時間和處理時間
    total_times = [req["total_time"] for req in completed_requests]
    queue_times = [req["queue_time"] for req in completed_requests]
    processing_times = [req["processing_time"] for req in completed_requests]

    # 計算統計數據
    return {
        "total_time": {
            "min": min(total_times),
            "max": max(total_times),
            "avg": sum(total_times) / len(total_times),
            "p50": sorted(total_times)[len(total_times) // 2],
            "p90": sorted(total_times)[int(len(total_times) * 0.9)],
            "p99": sorted(total_times)[int(len(total_times) * 0.99)]
        },
        "queue_time": {
            "min": min(queue_times),
            "max": max(queue_times),
            "avg": sum(queue_times) / len(queue_times)
        },
        "processing_time": {
            "min": min(processing_times),
            "max": max(processing_times),
            "avg": sum(processing_times) / len(processing_times)
        },
        "model_breakdown": _analyze_models(completed_requests)
    }


def _analyze_models(completed_requests):
    """分析模型性能差異"""
    model_stats = {}

    for req in completed_requests:
        model = req["model"]
        if model not in model_stats:
            model_stats[model] = {"count": 0, "total_times": [], "processing_times": []}

        model_stats[model]["count"] += 1
        model_stats[model]["total_times"].append(req["total_time"])
        model_stats[model]["processing_times"].append(req["processing_time"])

    # 計算每個模型的平均時間
    results = {}
    for model, stats in model_stats.items():
        results[model] = {
            "request_count": stats["count"],
            "avg_total_time": sum(stats["total_times"]) / len(stats["total_times"]),
            "avg_processing_time": sum(stats["processing_times"]) / len(stats["processing_times"])
        }

    return results


def _analyze_failures(failed_requests):
    """分析失敗原因"""
    if not failed_requests:
        return {"empty": True}

    # 統計錯誤類型
    error_types = {}
    for req in failed_requests:
        error = req.get("error", "Unknown")
        if error not in error_types:
            error_types[error] = 0
        error_types[error] += 1

    # 分析不同錯誤類型
    categorized_errors = {
        "timeout":
        len([req for req in failed_requests if "timeout" in req.get("error", "").lower()]),
        "connection":
        len([req for req in failed_requests if "connection" in req.get("error", "").lower()]),
        "auth":
        len([
            req for req in failed_requests
            if "auth" in req.get("error", "").lower() or "unauthorized" in req.get("error", "").lower()
        ]),
        "server_error":
        len([req for req in failed_requests if "500" in req.get("error", "")]),
        "other":
        len([
            req for req in failed_requests
            if not any(x in req.get("error", "").lower() for x in ["timeout", "connection", "auth", "500"])
        ])
    }

    return {
        "error_types": error_types,
        "categorized": categorized_errors,
        "examples": [req for req in failed_requests[:5]]  # 最多顯示5個例子
    }


def _calculate_stability_score(stats):
    """計算穩定性評分 (0-10)"""
    # 基本評分：10分
    score = 10.0

    # 根據失敗率扣分
    if stats["request_count"] > 0:
        failure_rate = stats["failure_count"] / stats["request_count"]
        if failure_rate > 0:
            # 失敗率每超過1%扣0.5分
            score -= min(5, failure_rate * 50)

    # 根據超時扣分
    if stats["timeout_count"] > 0:
        # 每個超時扣0.2分
        score -= min(3, stats["timeout_count"] * 0.2)

    # 檢查平均響應時間
    # 如果平均響應時間超過10秒，扣分
    if stats["success_count"] > 0 and stats["average_response_time"] > 10:
        score -= min(2, (stats["average_response_time"] - 10) * 0.2)

    # 最低分數為0
    return max(0, round(score, 1))


def _check_for_crash_indicators(failed_requests):
    """檢查是否有系統崩潰的跡象"""
    if not failed_requests:
        return False

    # 檢查連接相關錯誤的比例
    connection_errors = len([
        req for req in failed_requests
        if any(x in req.get("error", "").lower()
               for x in ["connection", "refused", "reset", "timeout", "EOF", "broken pipe"])
    ])

    # 如果連接錯誤佔所有失敗的50%以上，可能是系統崩潰
    connection_error_ratio = connection_errors / len(failed_requests) if len(failed_requests) > 0 else 0

    return connection_error_ratio >= 0.5 and connection_errors >= 5  # 至少有5個連接錯誤


if __name__ == "__main__":
    # 運行測試
    asyncio.run(main())
