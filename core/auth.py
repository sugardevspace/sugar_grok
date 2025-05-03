from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.setting import settings

security = HTTPBearer()

async def authenticate(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """驗證 API 請求"""
    if not credentials.credentials or credentials.credentials != settings.SERVER_API_KEY:
        raise HTTPException(status_code=401, detail="無效的認證憑證")
    return credentials.credentials