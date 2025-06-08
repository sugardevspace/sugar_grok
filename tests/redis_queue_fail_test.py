# tests/test_redis_docker_failure_fallback.py

import pytest
import subprocess
import time
import redis
import logging
from services.queue.redis_queue import RedisQueueManager
import asyncio

# 容器名稱
container_name = "redis-test-for-suger-ai"
image_name = "redis:latest"
# 取得一個 logger，透過 pytest 運行時就能看到 INFO 訊息
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def ensure_redis_container_running():
    """
    確保 Redis Docker 容器(redis-test)在測試開始前處於運行狀態：
      1. 如果該容器根本不存在，則自動執行 docker run 將它啟動。
      2. 如果容器已建立但目前未在運行中，則自動執行 docker start 啟動它。
      3. 如果已在運行，直接放行。
    無論哪種情況，都會印出 INFO 訊息說明採取了什麼行動，方便調試與日後追蹤。
    """
    # 1. 先檢查容器是否存在（inspect）
    res_inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
        capture_output=True,
        text=True
    )

    # 如果 inspect returncode != 0，代表容器不存在
    if res_inspect.returncode != 0:
        logger.info(f"Container '{container_name}' 不存在，正在自動建立並啟動新的 Redis 容器 …")
        # 執行 docker run
        run_res = subprocess.run(
            ["docker", "run", "-d", "--name", container_name, "-p", "6379:6379", image_name],
            capture_output=True,
            text=True
        )
        if run_res.returncode != 0:
            # 如果連 run 也失敗，就讓 pytest 失敗並顯示原因
            pytest.fail(
                f"無法自動建立並啟動 Redis 容器 '{container_name}'（映像: {image_name}）。\n"
                f"命令輸出: {run_res.stderr.strip()}"
            )
        else:
            container_id = run_res.stdout.strip()
            logger.info(f"成功啟動新的 Redis 容器 '{container_name}' (ID: {container_id})")
            # 給 Docker 幾秒鐘時間，確保 Redis 完全啟動並開始聆聽埠口
            time.sleep(1)
    else:
        # inspect 成功，表示容器已存在。接著檢查是否正在運行 (stdout 會是 "true" 或 "false")
        running_state = res_inspect.stdout.strip().lower()
        if running_state == "true":
            logger.info(f"Redis 容器 '{container_name}' 已在運行，直接放行。")
        else:
            logger.info(f"Redis 容器 '{container_name}' 已存在，但未在運行，正在自動啟動中 …")
            start_res = subprocess.run(["docker", "start", container_name], capture_output=True, text=True)
            if start_res.returncode != 0:
                pytest.fail(
                    f"無法啟動已存在但未運行的 Redis 容器 '{container_name}'。\n"
                    f"命令輸出: {start_res.stderr.strip()}"
                )
            else:
                logger.info(f"成功啟動 Redis 容器 '{container_name}'")
                time.sleep(1)

    # 最後放行，進入測試階段


@pytest.fixture(scope="function")
def test_redis():
    """
    提供一個連到本機 Redis db=1 的 client，並在前後 flushdb 清空資料。
    因為我們要用 Docker 內的 redis-test 容器，預設映射到 localhost:6379。
    """
    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    r.flushdb()
    yield r
    r.flushdb()


@pytest.fixture(scope="function")
def manager(test_redis):
    """
    建立一個 RedisQueueManager，並把它的 .redis 換成 test_redis (db=1)；
    此 stage 我們不 patch MemoryQueueManager，而是等到 Docker 停止時才自然走 fallback。
    """
    mgr = RedisQueueManager()
    mgr.redis = test_redis
    mgr.queue_key = "pytest_test_queue"
    return mgr


@pytest.mark.asyncio
async def test_docker_redis_failure_fallback(manager):
    """
    測試流程：
    1. Redis 正常 (db=1)，enqueue 3 筆高優先級 (priority=1)，再 dequeue 3 次，確認順序正確。
    2. 透過 subprocess 執行 'docker stop redis-test'，讓 Redis 容器完全關閉，模擬 Redis 掛掉。
    3. 休息 1 秒，確保容器已停，之後所有 redis 操作都會拋 ConnectionError。
    4. 继续 enqueue 3 筆低優先級 (priority=100) 資料，此時 enqueue 會嘗試重試 3 次都失敗，最後執行 MemoryQueueManager.enqueue，並回傳 mem_...。
    5. 從 MemoryQueueManager 依序 dequeue 3 次，確認順序符合 low_priority_data。
    6. 最後用 subprocess 執行 'docker start redis-test'，把容器重新啟動，恢復環境。
    """

    # -------------------------
    # 1) Redis 正常階段：插入並 dequeue 高優先級
    # -------------------------
    high_priority_data = [
        {"id_ext": 1, "msg": "high_A"},
        {"id_ext": 2, "msg": "high_B"},
        {"id_ext": 3, "msg": "high_C"},
    ]
    # 確保 Redis 佇列為空
    assert await manager.get_queue_length() == 0

    # 插入 3 筆高優先級
    for item in high_priority_data:
        high_req_ids = await manager.enqueue(item, 1)
        assert high_req_ids.startswith("req_")

    # 確認 長度 = 3
    assert await manager.get_queue_length() == 3
    # await asyncio.sleep(20)

    # -------------------------
    # 2) 模擬 Docker 內 Redis 突然失效：docker stop
    # -------------------------
    stop = subprocess.run(["docker", "stop", container_name], capture_output=True)
    assert stop.returncode == 0, f"停用 {container_name} 容器失敗: {stop.stderr.decode()}"
    # 等待 1 秒，確保容器已經完全關閉
    time.sleep(1)

    # 3) 現在 Redis 容器關閉，test_redis 連線會拋 ConnectionError
    #    繼續 enqueue 3 筆低優先級 (priority=100)，應 fallback 到 MemoryQueue
    low_priority_data = [
        {"id_ext": 4, "msg": "low_X"},
        {"id_ext": 5, "msg": "low_Y"},
        {"id_ext": 6, "msg": "low_Z"},
    ]
    low_req_ids = []
    for item in low_priority_data:
        # 由於 test_redis 連不上，manager.enqueue 內部會重試 3 次失敗後
        # self.redis = redis.Redis(...) 也因容器關閉再度失敗，最終執行 MemoryQueueManager.enqueue
        req_id = await manager.enqueue(item, priority=100)
        low_req_ids.append(req_id)

    # 因為REDIS炸了，所以在REDIS中的請求不見了，此時會剩三筆
    assert await manager.get_queue_length() == 3

    # -------------------------
    # 4) 恢復 Docker 內 Redis 容器
    # -------------------------
    start = subprocess.run(["docker", "start", container_name], capture_output=True)
    assert start.returncode == 0, f"重新啟動 {container_name} 容器失敗: {start.stderr.decode()}"
    # 等待 1 秒，確保容器已啟動
    time.sleep(1)

    # 確認 Redis 已恢復
    pong = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True).ping()
    assert pong is True, "應該能再 ping 到已重啟的 Redis"

    await asyncio.sleep(5)  # 確保已經升級
    assert manager.memory_queue is None
    assert manager.background_ping is None
    assert await manager.get_queue_length() == 6
    for i in range(3):
        assert (await manager.dequeue())["data"] in high_priority_data
    for i in range(3):
        assert (await manager.dequeue())["data"] in low_priority_data


@pytest.mark.asyncio
async def test_for_re_su(manager):
    target_logger = logging.getLogger("api_server")

    class TriggerHandler(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            logger.info("出現偵測")
            if "Redis 連接錯誤" in msg:
                # 一旦偵測到關鍵字，立刻執行 docker restart
                logger.info(f"Redis 容器 '{container_name}' 已存在，但未在運行，正在自動啟動中 …")
                start_res = subprocess.run(["docker", "start", container_name], capture_output=True, text=True)
                if start_res.returncode != 0:
                    pytest.fail(
                        f"無法啟動已存在但未運行的 Redis 容器 '{container_name}'。\n"
                        f"命令輸出: {start_res.stderr.strip()}"
                    )
                else:
                    logger.info(f"成功啟動 Redis 容器 '{container_name}'")
                    time.sleep(1)

    handler = TriggerHandler()
    handler.setLevel(logging.INFO)  # 只監聽 INFO 以上
    target_logger.addHandler(handler)
    # -------------------------
    # 1) Redis 正常階段：插入並 dequeue 高優先級
    # -------------------------
    high_priority_data = [
        {"id_ext": 1, "msg": "high_A"},
        {"id_ext": 2, "msg": "high_B"},
        {"id_ext": 3, "msg": "high_C"},
    ]
    # 確保 Redis 佇列為空
    assert await manager.get_queue_length() == 0

    # 插入 3 筆高優先級
    for item in high_priority_data:
        high_req_ids = await manager.enqueue(item, 1)
        assert high_req_ids.startswith("req_")

    # 確認 長度 = 3
    assert await manager.get_queue_length() == 3
    # await asyncio.sleep(20)

    # -------------------------
    # 2) 模擬 Docker 內 Redis 突然失效：docker stop
    # -------------------------
    stop = subprocess.run(["docker", "stop", container_name], capture_output=True)
    assert stop.returncode == 0, f"停用 {container_name} 容器失敗: {stop.stderr.decode()}"
    # 等待 1 秒，確保容器已經完全關閉
    await asyncio.sleep(1)

    # 3) 現在 Redis 容器關閉，test_redis 連線會拋 ConnectionError
    #    但重試成功，所以會正常塞redis
    low_priority_data = [
        {"id_ext": 4, "msg": "low_X"},
        {"id_ext": 5, "msg": "low_Y"},
        {"id_ext": 6, "msg": "low_Z"},
    ]
    low_req_ids = []
    for item in low_priority_data:
        req_id = await manager.enqueue(item, priority=100)
        low_req_ids.append(req_id)

    assert await manager.get_queue_length() == 6
