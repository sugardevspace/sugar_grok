# 使用 Python 3.12 作為基礎映像
FROM python:3.12-slim

# 設定工作目錄
WORKDIR /app

# 安裝 Redis (可選，如果您想在同一容器中執行 Redis)
# 如果您計劃使用 Zeabur 的 Redis 服務，可以刪除這部分
RUN apt-get update && apt-get install -y redis-server

# 複製依賴文件並安裝依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程序代碼
COPY . .

# 啟動腳本
RUN echo '#!/bin/bash\n\
# 啟動 Redis（如果使用外部 Redis，請刪除此行）\n\
redis-server --daemonize yes\n\
# 啟動 API 服務器\n\
python main.py\n\
' > /app/start.sh

RUN chmod +x /app/start.sh

# 暴露 API 端口
EXPOSE 8000

# 啟動命令
CMD ["/app/start.sh"]