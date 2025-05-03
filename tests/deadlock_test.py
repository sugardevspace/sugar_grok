#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API服務器死鎖檢測測試
此測試檔案專門用於檢測可能導致系統死鎖或無限等待的情況
"""

import asyncio
import aiohttp
import time
import random
import argparse
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Set

# 配置日誌
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(f"deadlock_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger('deadlock_test')

# 測試配置
DEFAULT_CONFIG = {
    "base_url": "http://localhost:8000/v1",  # API 基礎 URL
    "api_key": "your_api_key",  # 替換為您的 API 金鑰
    "test_duration": 300,  # 測試持續時間（秒）
    "timeout": 60,  # 請求超時（秒）
    "lock_test_interval": 0.1,  # 鎖測試間隔（秒）
}


class DeadlockTest:
    """API 服務器死鎖檢測測試類"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化測試
        
        Args:
            config: 測試配置
        """
        self.config = config
        self.base_url = config["base_url"]
        self.api_key = config["api_key"]
        self.test_duration = config["test_duration"]
        self.timeout = config["timeout"]
        self.lock_test_interval = config["lock_test_interval"]

        # 同步檢測相關
        self.active_failures = 0
        self.consecutive_failures = 0
        self.max_consecutive_failures = 0

        # 存儲系統狀態檢查結果
        self.status_checks = []
        self.lock_tests = []

        # 控制標誌
        self.running = False
        self.detected_deadlock = False
        self.detected_starvation = False

        # 任務控制
        self.tasks = []

        logger.info(f"死鎖檢測測試配置: {json.dumps(config, indent=2, ensure_ascii=False)}")

    async def run_test(self):
        """運行完整測試"""
        self.running = True
        start_time = time.time()
        end_time = start_time + self.test_duration

        logger.info(f"開始死鎖檢測測試，持續時間: {self.test_duration} 秒")

        # 啟動各種檢測任務
        self.tasks = [
            asyncio.create_task(self._rapid_failover_test()),
            asyncio.create_task(self._rapid_status_checks()),
            asyncio.create_task(self._concurrent_lock_test()),
            asyncio.create_task(self._long_request_test()),
            asyncio.create_task(self._system_resource_monitor())
        ]

        # 等待測試時間結束
        try:
            await asyncio.sleep(self.test_duration)
        except asyncio.CancelledError:
            logger.warning("測試被提前取消")

        # 停止所有任務
        self.running = False
        for task in self.tasks:
            if not task.done():
                task.cancel()

        # 等待任務完成
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        # 分析結果
        results = self._analyze_results()
        self._print_test_results(results)

        return results

    async def _rapid_failover_test(self):
        """
        快速故障切換測試
        在短時間內頻繁切換提供者，測試故障切換機制的穩定性
        """
        try:
            # 首先獲取所有提供者
            providers = await self._get_providers()
            if not providers or "providers" not in providers or len(providers["providers"]) < 2:
                logger.warning("提供者數量不足，無法進行故障切換測試")
                return

            available_providers = providers["providers"]
            current_provider = providers.get("current_provider")

            # 記錄當前提供者，以便測試結束後恢復
            original_provider = current_provider

            # 短時間內進行多次故障切換
            switch_count = 0
            failure_count = 0
            max_switches = min(50, self.test_duration // 2)  # 最多切換50次或測試時間的一半

            for i in range(max_switches):
                if not self.running:
                    break

                # 選擇一個不同的提供者
                target_provider = random.choice([p for p in available_providers if p != current_provider])

                try:
                    # 嘗試切換
                    success = await self._force_failover(target_provider)
                    if success:
                        logger.debug(f"故障切換成功: {current_provider} -> {target_provider}")
                        current_provider = target_provider
                        switch_count += 1
                    else:
                        logger.warning(f"故障切換失敗: {current_provider} -> {target_provider}")
                        failure_count += 1
                except Exception as e:
                    logger.error(f"故障切換時發生錯誤: {str(e)}")
                    failure_count += 1

                # 短暫等待
                await asyncio.sleep(random.uniform(1.0, 3.0))

            # 嘗試恢復原始提供者
            if current_provider != original_provider:
                try:
                    await self._force_failover(original_provider)
                    logger.info(f"已恢復原始提供者: {original_provider}")
                except Exception as e:
                    logger.error(f"恢復原始提供者時發生錯誤: {str(e)}")

            logger.info(f"故障切換測試完成: 成功切換 {switch_count} 次，失敗 {failure_count} 次")

        except asyncio.CancelledError:
            logger.info("故障切換測試被取消")
            raise
        except Exception as e:
            logger.error(f"故障切換測試發生錯誤: {str(e)}")

    async def _rapid_status_checks(self):
        """
        快速狀態檢查測試
        在短時間內頻繁檢查系統狀態，測試系統在高頻狀態查詢下的穩定性
        """
        try:
            check_interval = 0.5  # 檢查間隔（秒）
            total_checks = 0
            failed_checks = 0

            while self.running:
                try:
                    # 帶超時的狀態檢查
                    status = await asyncio.wait_for(self._check_system_status(), timeout=10.0)

                    if status:
                        total_checks += 1
                        self.status_checks.append({
                            "timestamp":
                            time.time(),
                            "success":
                            True,
                            "queue_length":
                            status.get("queue_status", {}).get("current_length", -1),
                            "failover_mode":
                            status.get("failover_status", {}).get("in_failover_mode", False)
                        })

                        # 重置連續失敗計數
                        self.consecutive_failures = 0
                    else:
                        failed_checks += 1
                        self.consecutive_failures += 1
                        self.max_consecutive_failures = max(self.max_consecutive_failures, self.consecutive_failures)

                        self.status_checks.append({
                            "timestamp": time.time(),
                            "success": False,
                            "error": "Empty response"
                        })

                except asyncio.TimeoutError:
                    failed_checks += 1
                    self.consecutive_failures += 1
                    self.max_consecutive_failures = max(self.max_consecutive_failures, self.consecutive_failures)

                    self.status_checks.append({"timestamp": time.time(), "success": False, "error": "Timeout"})

                    logger.warning(f"狀態檢查超時 (連續失敗: {self.consecutive_failures})")

                except Exception as e:
                    failed_checks += 1
                    self.consecutive_failures += 1
                    self.max_consecutive_failures = max(self.max_consecutive_failures, self.consecutive_failures)

                    self.status_checks.append({"timestamp": time.time(), "success": False, "error": str(e)})

                    logger.warning(f"狀態檢查失敗: {str(e)} (連續失敗: {self.consecutive_failures})")

                # 檢測是否可能發生死鎖
                if self.consecutive_failures >= 5:
                    self.detected_deadlock = True
                    logger.critical(f"可能發生死鎖: {self.consecutive_failures} 次連續失敗")

                # 等待下一次檢查
                await asyncio.sleep(check_interval)

            logger.info(f"狀態檢查測試完成: 總檢查 {total_checks} 次，失敗 {failed_checks} 次")

        except asyncio.CancelledError:
            logger.info("狀態檢查測試被取消")
            raise
        except Exception as e:
            logger.error(f"狀態檢查測試發生錯誤: {str(e)}")

    async def _concurrent_lock_test(self):
        """
        併發鎖測試
        模擬多個並發任務同時嘗試獲取故障切換管理器的鎖
        """
        try:
            # 同時運行多個 API 調用，這些調用會嘗試獲取相同的鎖
            concurrency = 5  # 同時 5 個請求
            iterations = max(100, self.test_duration // 3)  # 最多執行100次或測試時間的1/3

            for i in range(iterations):
                if not self.running:
                    break

                # 創建多個並發請求
                tasks = []
                for j in range(concurrency):
                    if j % 2 == 0:
                        # 檢查系統狀態 (會獲取故障切換鎖)
                        tasks.append(asyncio.create_task(self._timed_system_status()))
                    else:
                        # 獲取提供者列表 (也可能獲取鎖)
                        tasks.append(asyncio.create_task(self._timed_providers()))

                # 等待所有請求完成或超時
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 分析結果
                lock_timings = []
                for result in results:
                    if isinstance(result, Exception):
                        logger.warning(f"鎖測試請求異常: {str(result)}")
                    elif isinstance(result, dict) and "timing" in result:
                        lock_timings.append(result["timing"])

                # 記錄此批次的結果
                if lock_timings:
                    avg_time = sum(lock_timings) / len(lock_timings)
                    max_time = max(lock_timings)

                    self.lock_tests.append({
                        "timestamp": time.time(),
                        "iteration": i,
                        "timings": lock_timings,
                        "avg_time": avg_time,
                        "max_time": max_time
                    })

                    # 檢測是否存在鎖爭用問題
                    if max_time > 5.0:  # 如果有請求超過5秒
                        logger.warning(f"可能存在鎖爭用問題: 最長等待時間 {max_time:.2f} 秒")
                        self.detected_starvation = True

                # 等待一段時間再進行下一輪測試
                await asyncio.sleep(self.lock_test_interval)

            logger.info(f"併發鎖測試完成: 執行 {len(self.lock_tests)} 輪測試")

        except asyncio.CancelledError:
            logger.info("併發鎖測試被取消")
            raise
        except Exception as e:
            logger.error(f"併發鎖測試發生錯誤: {str(e)}")

    async def _long_request_test(self):
        """
        長時間運行請求測試
        測試系統處理長時間運行任務的能力
        """
        try:
            # 發送一些需要長時間處理的複雜請求
            num_requests = max(5, self.test_duration // 60)  # 最多發送5個或測試時間的1/60
            completed = 0
            failed = 0

            for i in range(num_requests):
                if not self.running:
                    break

                try:
                    # 創建一個複雜的聊天請求
                    request_data = {
                        "model":
                        "gpt-4o" if i % 2 == 0 else "grok-3",  # 交替使用不同模型
                        "messages": [{
                            "role":
                            "user",
                            "content": ("請詳細解釋 TCP/IP 協議棧的所有層次和主要協議。"
                                        "包括每層的主要功能，常見問題和解決方案。"
                                        "並討論在高並發系統中如何優化網絡性能。"
                                        "最後分析分布式系統中的網絡延遲問題。")
                        }],
                        "temperature":
                        0.7,
                        "max_tokens":
                        8000  # 請求大量文字
                    }

                    # 發送請求並跟踪
                    response = await self._send_chat_request(request_data)
                    if response and "request_id" in response:
                        # 獲取請求ID並檢查結果
                        request_id = response["request_id"]
                        logger.info(f"長請求 {i+1}/{num_requests} 已加入佇列: {request_id}")

                        # 定期檢查請求狀態
                        result = await self._wait_for_request_completion(request_id)
                        if result and result.get("status") == "completed":
                            completed += 1
                            logger.info(f"長請求 {i+1}/{num_requests} 完成: {request_id}")
                        else:
                            failed += 1
                            logger.warning(f"長請求 {i+1}/{num_requests} 失敗: {request_id}")
                    else:
                        failed += 1
                        logger.warning(f"長請求 {i+1}/{num_requests} 加入佇列失敗")

                except Exception as e:
                    failed += 1
                    logger.error(f"長請求 {i+1}/{num_requests} 發生錯誤: {str(e)}")

                # 等待一段時間再發送下一個請求
                await asyncio.sleep(10)  # 10秒間隔

            logger.info(f"長請求測試完成: 發送 {num_requests} 個請求，完成 {completed} 個，失敗 {failed} 個")

        except asyncio.CancelledError:
            logger.info("長請求測試被取消")
            raise
        except Exception as e:
            logger.error(f"長請求測試發生錯誤: {str(e)}")

    async def _system_resource_monitor(self):
        """
        系統資源監控
        定期檢查系統資源使用情況
        """
        try:
            check_interval = 15  # 每15秒檢查一次
            queue_lengths = []

            while self.running:
                try:
                    # 檢查佇列長度
                    status = await self._check_system_status()
                    if status:
                        queue_length = status.get("queue_status", {}).get("current_length", -1)
                        queue_lengths.append(queue_length)

                        # 檢查佇列是否在增長
                        if len(queue_lengths) >= 3 and all(queue_lengths[-3:][i] < queue_lengths[-3:][i + 1]
                                                           for i in range(2)):
                            logger.warning(f"佇列持續增長: {queue_lengths[-3:]}")

                except Exception as e:
                    logger.warning(f"資源監控失敗: {str(e)}")

                # 等待下一次檢查
                await asyncio.sleep(check_interval)

            # 分析佇列趨勢
            if len(queue_lengths) >= 5:
                queue_trend = "上升" if queue_lengths[-1] > queue_lengths[0] else "下降或穩定"
                logger.info(f"佇列長度趨勢: {queue_trend}, 起始: {queue_lengths[0]}, 結束: {queue_lengths[-1]}")

        except asyncio.CancelledError:
            logger.info("資源監控被取消")
            raise
        except Exception as e:
            logger.error(f"資源監控發生錯誤: {str(e)}")

    async def _force_failover(self, provider: str) -> bool:
        """
        強制切換到指定的提供者
        
        Args:
            provider: 提供者名稱
            
        Returns:
            bool: 是否成功切換
        """
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/system/force-failover/{provider}",
                                        headers=headers,
                                        timeout=10) as response:
                    if response.status == 200:
                        return True
                    else:
                        logger.warning(f"強制切換失敗: HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"強制切換發生錯誤: {str(e)}")
            return False

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
            logger.error(f"獲取提供者列表時發生錯誤: {str(e)}")
            return {}

    async def _check_system_status(self) -> Optional[Dict[str, Any]]:
        """
        檢查系統狀態
        
        Returns:
            Optional[Dict[str, Any]]: 系統狀態
        """
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/system/status", headers=headers, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"獲取系統狀態失敗: HTTP {response.status}")
                        return None
        except Exception as e:
            logger.error(f"獲取系統狀態時發生錯誤: {str(e)}")
            return None

    async def _timed_system_status(self) -> Dict[str, Any]:
        """
        計時測試系統狀態API調用
        
        Returns:
            Dict[str, Any]: 包含時間的結果
        """
        start_time = time.time()
        try:
            result = await self._check_system_status()
            elapsed = time.time() - start_time
            return {"success": result is not None, "timing": elapsed, "type": "status"}
        except Exception as e:
            elapsed = time.time() - start_time
            return {"success": False, "error": str(e), "timing": elapsed, "type": "status"}

    async def _timed_providers(self) -> Dict[str, Any]:
        """
        計時測試獲取提供者列表API調用
        
        Returns:
            Dict[str, Any]: 包含時間的結果
        """
        start_time = time.time()
        try:
            result = await self._get_providers()
            elapsed = time.time() - start_time
            return {"success": bool(result), "timing": elapsed, "type": "providers"}
        except Exception as e:
            elapsed = time.time() - start_time
            return {"success": False, "error": str(e), "timing": elapsed, "type": "providers"}

    async def _send_chat_request(self, request_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        發送聊天請求
        
        Args:
            request_data: 請求數據
            
        Returns:
            Optional[Dict[str, Any]]: 響應數據
        """
        try:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/chat/completions",
                                        json=request_data,
                                        headers=headers,
                                        timeout=self.timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error_text = await response.text()
                        logger.warning(f"聊天請求失敗: HTTP {response.status} - {error_text}")
                        return None
        except Exception as e:
            logger.error(f"發送聊天請求時發生錯誤: {str(e)}")
            return None

    async def _wait_for_request_completion(self, request_id: str, max_wait: int = 180) -> Optional[Dict[str, Any]]:
        """
        等待請求完成
        
        Args:
            request_id: 請求ID
            max_wait: 最大等待時間（秒）
            
        Returns:
            Optional[Dict[str, Any]]: 完成的響應或None表示超時
        """
        start_time = time.time()
        check_interval = 5  # 每5秒檢查一次

        while time.time() - start_time < max_wait:
            try:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.base_url}/requests/{request_id}", headers=headers,
                                           timeout=10) as response:
                        if response.status == 200:
                            response_data = await response.json()
                            status = response_data.get("status")

                            if status in ["completed", "error"]:
                                return response_data

                            # 仍在處理中，等待並再次檢查
                            logger.debug(f"請求 {request_id} 仍在處理中，等待下一次檢查")
                        else:
                            logger.warning(f"檢查請求 {request_id} 狀態失敗: HTTP {response.status}")

            except Exception as e:
                logger.warning(f"檢查請求 {request_id} 狀態時發生錯誤: {str(e)}")

            # 等待下一次檢查
            await asyncio.sleep(check_interval)

        logger.warning(f"等待請求 {request_id} 完成超時 (超過 {max_wait} 秒)")
        return None

    def _analyze_results(self) -> Dict[str, Any]:
        """
        分析測試結果
        
        Returns:
            Dict[str, Any]: 測試結果分析
        """
        # 計算系統狀態檢查的成功率
        total_status_checks = len(self.status_checks)
        successful_status_checks = sum(1 for check in self.status_checks if check.get("success", False))
        status_success_rate = successful_status_checks / total_status_checks if total_status_checks > 0 else 0

        # 分析鎖測試結果
        lock_timing_stats = {
            "count": len(self.lock_tests),
            "avg_times": [test.get("avg_time", 0) for test in self.lock_tests],
            "max_times": [test.get("max_time", 0) for test in self.lock_tests],
        }

        if lock_timing_stats["avg_times"]:
            lock_timing_stats["overall_avg"] = sum(lock_timing_stats["avg_times"]) / len(lock_timing_stats["avg_times"])
            lock_timing_stats["overall_max"] = max(
                lock_timing_stats["max_times"]) if lock_timing_stats["max_times"] else 0
        else:
            lock_timing_stats["overall_avg"] = 0
            lock_timing_stats["overall_max"] = 0

        # 檢測結果
        detection_results = {
            "deadlock_detected": self.detected_deadlock,
            "lock_starvation_detected": self.detected_starvation,
            "max_consecutive_failures": self.max_consecutive_failures,
            "status_check_success_rate": status_success_rate,
        }

        # 死鎖風險評估
        deadlock_risk = "低"
        if self.detected_deadlock:
            deadlock_risk = "高"
        elif self.detected_starvation or self.max_consecutive_failures >= 3:
            deadlock_risk = "中"

        return {
            "status_checks": {
                "total": total_status_checks,
                "successful": successful_status_checks,
                "success_rate": status_success_rate,
            },
            "lock_test": lock_timing_stats,
            "detection": detection_results,
            "deadlock_risk": deadlock_risk,
        }

    def _print_test_results(self, results: Dict[str, Any]):
        """
        輸出測試結果
        
        Args:
            results: 測試結果
        """
        logger.info("=" * 50)
        logger.info("死鎖測試結果摘要")
        logger.info("=" * 50)

        # 系統狀態檢查
        status_checks = results["status_checks"]
        logger.info(f"系統狀態檢查: 總計 {status_checks['total']} 次, 成功率 {status_checks['success_rate']:.2%}")

        # 鎖測試
        lock_test = results["lock_test"]
        logger.info(f"併發鎖測試: 執行 {lock_test['count']} 輪")
        logger.info(f"平均響應時間: {lock_test['overall_avg']:.2f} 秒")
        logger.info(f"最長等待時間: {lock_test['overall_max']:.2f} 秒")

        # 檢測結果
        detection = results["detection"]
        logger.info(f"死鎖檢測: {'是' if detection['deadlock_detected'] else '否'}")
        logger.info(f"鎖爭用問題: {'是' if detection['lock_starvation_detected'] else '否'}")
        logger.info(f"最大連續失敗次數: {detection['max_consecutive_failures']}")

        # 風險評估
        logger.info(f"死鎖風險評估: {results['deadlock_risk']}")

        if results["deadlock_risk"] == "高":
            logger.warning("系統存在明顯的死鎖或嚴重的併發問題，需要立即修復")
        elif results["deadlock_risk"] == "中":
            logger.warning("系統存在潛在的併發問題或鎖爭用，建議優化")
        else:
            logger.info("系統在併發處理方面表現良好，未檢測到明顯問題")

        logger.info("=" * 50)


async def main():
    """主函數"""
    parser = argparse.ArgumentParser(description="API 服務器死鎖檢測測試")
    parser.add_argument("--base-url", help="API 基礎 URL", default=DEFAULT_CONFIG["base_url"])
    parser.add_argument("--api-key", help="API 金鑰", default=DEFAULT_CONFIG["api_key"])
    parser.add_argument("--duration", type=int, help="測試持續時間（秒）", default=DEFAULT_CONFIG["test_duration"])
    parser.add_argument("--timeout", type=int, help="請求超時（秒）", default=DEFAULT_CONFIG["timeout"])

    args = parser.parse_args()

    # 更新配置
    config = DEFAULT_CONFIG.copy()
    config.update({
        "base_url": args.base_url,
        "api_key": args.api_key,
        "test_duration": args.duration,
        "timeout": args.timeout,
    })

    # 創建並運行測試
    test = DeadlockTest(config)
    results = await test.run_test()

    # 將結果保存到文件
    filename = f"deadlock_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    logger.info(f"詳細測試結果已保存到 {filename}")

    return results


if __name__ == "__main__":
    # 運行測試
    asyncio.run(main())
