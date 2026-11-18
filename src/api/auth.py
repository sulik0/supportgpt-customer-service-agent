"""员工注册、登录和当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.auth.jwt import create_access_token, get_password_hash, verify_password
from src.auth.rbac import get_current_user, require_admin
from src.config import settings
from src.database import get_db
from src.models.db_models import User
from src.models.schemas import LoginRequest, Token, UserCreate, UserResponse
from src.security import public_access_service, public_rate_limiter

router = APIRouter()


# --- AUTH ENDPOINTS ---
async def _create_staff_user(user_in: UserCreate, db: AsyncSession) -> User:
    """集中创建员工账号，避免公开注册与管理接口逻辑漂移。"""
    if (
        settings.APP_ENV.lower() in {"production", "prod"}
        and len(user_in.password) < 12
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Production staff passwords must contain at least 12 characters.",
        )
    existing = await db.execute(select(User).filter(User.username == user_in.username))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    new_user = User(
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
        role=user_in.role,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.post(
    "/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    if not settings.STAFF_SELF_REGISTRATION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _create_staff_user(user_in, db)


@router.post(
    "/auth/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def create_staff_user(
    user_in: UserCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """只允许管理员创建 agent、manager 或 admin 账号。"""
    return await _create_staff_user(user_in, db)


@router.post("/auth/token", response_model=Token)
async def login_for_access_token(
    form_data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await public_rate_limiter.enforce(
        scope="staff-login",
        identities=public_access_service.login_identity(request, form_data.username),
        limit=settings.AUTH_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    result = await db.execute(select(User).filter(User.username == form_data.username))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@router.get("/auth/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
