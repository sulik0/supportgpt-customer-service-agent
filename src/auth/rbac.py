from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional

from src.database import get_db
from src.models.db_models import User
from src.auth.jwt import decode_access_token

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def _resolve_user(
    credentials: HTTPAuthorizationCredentials, db: AsyncSession
) -> User:
    """解析 Bearer Token 并加载对应用户。"""
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str = payload.get("sub")
    role: str = payload.get("role")
    if username is None or role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token claims",
        )

    result = await db.execute(select(User).filter(User.username == username))
    user = result.scalars().first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency to retrieve the currently logged in user."""
    return await _resolve_user(credentials, db)


async def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """允许匿名访问，但在提供 Token 时完成完整身份校验。"""
    if credentials is None:
        return None
    return await _resolve_user(credentials, db)


class RoleChecker:
    """负责校验当前用户是否具备接口要求的 RBAC 角色。"""

    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted. Required roles: {self.allowed_roles}",
            )
        return current_user


# Convenience instances for dependencies
require_admin = RoleChecker(["admin"])
require_manager = RoleChecker(["admin", "manager"])
require_agent = RoleChecker(["admin", "manager", "agent"])
