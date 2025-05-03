# Grok API 服務器 MVP

這是一個基於 FastAPI 的 Grok API 服務器，專為處理 AI 角色聊天應用的請求而設計。它實現了請求隊列、速率限制、多 API 金鑰管理、成本監控和錯誤處理等功能。

## 核心功能

- ✅ 基本的 Grok API 調用功能
- ✅ 請求隊列系統 (支援 Redis 或記憶體內隊列)
- ✅ 速率限制器 (使用令牌桶算法)
- ✅ 多 API 金鑰輪換策略
- ✅ 指數退避重試機制
- ✅ 成本監控與統計
- ✅ 基本的認證機制
- ✅ 完整的日誌記錄

## 系統需求

- Python 3.8+
- Redis (可選，但建議使用)
- Grok API 金鑰

## 安裝與設定

1. 複製代碼倉庫：

```bash
git clone https://github.com/your-username/grok-api-server.git
cd grok-api-server
```

2. 安裝依賴：

```bash
pip install -r requirements.txt
```

3. 創建 `.env` 文件並設定您的 API 金鑰：

```
# Grok API 金鑰列表，用逗號分隔
GROK_API_KEYS=your_grok_api_key_1,your_grok_api_key_2,your_grok_api_key_3

# 服務器 API 金鑰，用於客戶端認證
SERVER_API_KEY=your_server_api_key_here

# Redis 設定 (可選)
REDIS_HOST=localhost
REDIS_PORT=6379
```

4. 啟動服務器：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## 使用 Docker 部署

您也可以使用 Docker 來部署服務器：

```bash
docker build -t grok-api-server .
docker run -p 8000:8000 --env-file .env grok-api-server
```

## API 端點

### 1. 發送聊天請求

```
POST /v1/chat/completions
```

請求範例：

```json
{
  "model": "grok-2-1212",
  "messages": [
    {
      "role": "system",
      "content": "你是一個有用的AI助手。"
    },
    {
      "role": "user",
      "content": "你好，請介紹一下台灣。"
    }
  ],
  "temperature": 0.7,
  "max_tokens": 1000
}
```

您需要在請求頭中包含授權：

```
Authorization: Bearer your_server_api_key_here
```

### 2. 檢查請求狀態 (需要 Redis)

```
GET /v1/requests/{request_id}
```

### 3. 獲取 API 使用統計

```
GET /v1/stats
```

## 監控和維護

- 查看日誌文件 `grok_api_server.log` 以追蹤系統運行情況
- 使用 `/v1/stats` 端點監控 API 使用情況、成本和錯誤率
- 定期檢查 API 金鑰使用情況，確保資源平衡分配

## 擴展建議

在 MVP 階段之後，您可以考慮以下擴展：

1. 實現更完善的用戶認證和授權系統
2. 添加更高級的請求優先級隊列
3. 實現更智能的 API 金鑰選擇策略（基於使用情況和錯誤率）
4. 添加更詳細的指標和監控儀表板
5. 實現更複雜的錯誤處理和回退策略
6. 擴展為微服務架構以提高可擴展性

## 許可證

MIT