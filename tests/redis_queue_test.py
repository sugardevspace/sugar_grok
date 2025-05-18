import os
import asyncio
import pytest
import json
import redis
from services.queue.redis_queue import RedisQueueManager
from core.setting import settings

# ---------- Fixtures ----------

@pytest.fixture(scope="function")
def test_redis():
    """
    建立一個空的資料庫實例
    """
    # 使用測試用資料庫與變數
    os.environ["REDIS_DB"] = "1"
    os.environ["REDIS_QUEUE_KEY"] = "pytest_queue"
    os.environ["REDIS_RESPONSE_PREFIX"] = "pytest_resp_"

    # 連線Redis
    r = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=int(os.environ["REDIS_DB"]),
        decode_responses=True
    )
    # 清空
    r.flushdb()

    yield r

    # 當測試結束時清空
    r.flushdb()

@pytest.fixture(scope="function")
def queue_mgr(test_redis):
    """
    提供使用測試資料庫的 RedisQueueManager 實例
    """
    mgr = RedisQueueManager()
    # 使用測試資料庫覆寫
    mgr.redis = test_redis
    mgr.queue_key = os.environ["REDIS_QUEUE_KEY"]
    mgr.response_prefix = os.environ["REDIS_RESPONSE_PREFIX"]
    return mgr

# ---------- 單元測試 ----------

@pytest.mark.asyncio
async def test_enqueue_and_dequeue(queue_mgr):
    # Enqueue 且驗證回應資料
    req_data = {"foo": "bar"}
    req_id = await queue_mgr.enqueue(req_data)
    assert req_id.startswith("req_")

    # Queue 大小應為1
    length = await queue_mgr.get_queue_length()
    assert length == 1

    # Dequeue 與驗證資料
    item = await queue_mgr.dequeue()
    assert item["id"] == req_id
    assert item["data"] == req_data

    # Queue 應為空
    assert await queue_mgr.dequeue() is None

@pytest.mark.asyncio
async def test_store_and_get_response(queue_mgr):
    req_id = "req_test_123"
    resp = {"ok": True, "value": 42}
    await queue_mgr.store_response(req_id, resp)
    got = await queue_mgr.get_response(req_id)
    assert got is not None
    assert json.loads(got) == resp

# ---------- Benchmarked Stress Test ----------

def test_stress_enqueue_benchmark(benchmark, queue_mgr):
    TOTAL_MESSAGES = 5000
    CONCURRENCY = 50

    # 定義同步 callable：每次執行都會跑完一次完整的批量併發 enqueue
    def run_bulk_enqueue():
        async def bulk_enqueue():
            sem = asyncio.Semaphore(CONCURRENCY)

            async def push(i):
                async with sem:
                    await queue_mgr.enqueue({"index": i, "payload": f"value_{i}"})

            tasks = [asyncio.create_task(push(i)) for i in range(TOTAL_MESSAGES)]
            await asyncio.gather(*tasks)

        # 清空佇列以確保每輪測量都是在「空佇列」起頭
        # 這裡直接呼叫同步 redis.flushdb() 來清空
        queue_mgr.redis.flushdb()
        asyncio.run(bulk_enqueue())

    # 讓 benchmark 自動執行多輪 run_bulk_enqueue()
    benchmark(run_bulk_enqueue)

    # 測完之後，為了不影響後續測試（如果有的話），可再額外清空
    queue_mgr.redis.flushdb()