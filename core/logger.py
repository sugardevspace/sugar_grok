import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging():
    """設定應用程式日誌"""
    # 確保日誌目錄存在
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, "api_server.log")

    # 設定基本日誌
    logger = logging.getLogger("api_server")
    logger.setLevel(logging.INFO)

    # 檔案處理器 (使用 RotatingFileHandler 來限制檔案大小)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5
    )
    file_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_format)

    # 控制台處理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(file_format)

    # 添加處理器到記錄器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# 獲取記錄器實例
logger = setup_logging()
