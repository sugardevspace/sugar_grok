import asyncio
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.setting import settings
from core.logger import setup_logging
from api.routes import router
from services.processor import start_queue_processor
from services.health_checker import get_health_checker
from services.metrics_service import get_metrics_service

# 設定日誌
logger = setup_logging()

# 定義生命週期管理上下文管理器


@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用程式生命週期管理"""
    try:
        # 啟動事件
        # 啟動背景佇列處理器
        asyncio.create_task(start_queue_processor())

        # 啟動健康檢查服務
        if settings.ENABLE_HEALTH_CHECKER:
            health_checker = get_health_checker()
            await health_checker.start()

        # 啟動指標服務
        if settings.ENABLE_METRICS:
            metrics_service = get_metrics_service()
            await metrics_service.start()

        logger.info(f"{settings.APP_TITLE} 服務已啟動")

        yield  # 控制權交給應用程式

    finally:
        # 關閉事件
        # 停止健康檢查服務
        if settings.ENABLE_HEALTH_CHECKER:
            health_checker = get_health_checker()
            await health_checker.stop()

        # 停止指標服務
        if settings.ENABLE_METRICS:
            metrics_service = get_metrics_service()
            await metrics_service.stop()

        logger.info(f"{settings.APP_TITLE} 服務已關閉")

# 初始化 FastAPI 應用，使用新的生命週期管理器
app = FastAPI(
    title=settings.APP_TITLE,
    lifespan=lifespan
)

# 設定 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 註冊路由
app.include_router(router, prefix="/v1")

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST,
                port=settings.PORT, reload=settings.DEBUG)
