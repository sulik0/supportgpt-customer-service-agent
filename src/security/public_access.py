"""公开 Demo 的匿名访客身份与会话隔离。"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from fastapi import HTTPException, Request, Response, status

from src.config import settings


_VISITOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,80}$")


class PublicAccessService:
    """签发 HttpOnly 访客 Cookie，并将公开会话限定在该访客内。"""

    def visitor_id(self, request: Request, response: Response) -> str:
        """验证现有 Cookie；无效或缺失时签发新的匿名身份。"""
        cookie_name = settings.PUBLIC_VISITOR_COOKIE_NAME
        raw_cookie = request.cookies.get(cookie_name, "")
        visitor_id = self._verify(raw_cookie)
        if visitor_id:
            return visitor_id

        visitor_id = secrets.token_urlsafe(24)
        response.set_cookie(
            key=cookie_name,
            value=self._signed(visitor_id),
            max_age=settings.PUBLIC_VISITOR_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            secure=settings.APP_ENV.lower() in {"production", "prod"},
            samesite=(
                "none"
                if settings.APP_ENV.lower() in {"production", "prod"}
                else "lax"
            ),
            path="/",
        )
        return visitor_id

    def validate_profile(self, profile_id: str) -> None:
        """公开入口只允许预置虚构画像，禁止枚举真实客户。"""
        if (
            settings.PUBLIC_DEMO_ISOLATION_ENABLED
            and profile_id not in settings.public_demo_profile_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Unsupported demo customer profile.",
            )

    def scoped_session_id(self, visitor_id: str, session_id: str) -> str:
        """用服务端密钥派生内部会话 ID，不向前端暴露访客标识。"""
        if not settings.PUBLIC_DEMO_ISOLATION_ENABLED:
            return session_id
        digest = hmac.new(
            self._secret(),
            f"{visitor_id}:{session_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"public-{digest}"

    @staticmethod
    def client_ip(request: Request) -> str:
        """取真实客户端 IP；直连地址在反向代理后是代理节点，会让按 IP 限流失效。

        只读代理写入的值：X-Real-IP，或 X-Forwarded-For 最右一段（最后一跳代理追加）。
        最左一段由客户端自带，可伪造，不能用于限流。
        """
        direct = request.client.host if request.client else "unknown"
        if not settings.TRUST_PROXY_HEADERS:
            return direct
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        forwarded = request.headers.get("x-forwarded-for", "")
        return forwarded.rsplit(",", 1)[-1].strip() or direct

    def rate_limit_identities(self, request: Request, visitor_id: str) -> tuple[str, ...]:
        """同时按 IP 和访客限流，降低删 Cookie 绕过限额的风险。"""
        client_ip = self.client_ip(request)
        return (
            self._opaque_identifier("ip", client_ip),
            self._opaque_identifier("visitor", visitor_id),
        )

    def login_identity(self, request: Request, username: str) -> tuple[str, ...]:
        """登录失败限额按 IP 和用户名分别约束。"""
        client_ip = self.client_ip(request)
        return (
            self._opaque_identifier("login-ip", client_ip),
            self._opaque_identifier("login-user", username.strip().lower()),
        )

    def _signed(self, visitor_id: str) -> str:
        signature = hmac.new(
            self._secret(), visitor_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{visitor_id}.{signature}"

    def _verify(self, value: str) -> str | None:
        try:
            visitor_id, signature = value.rsplit(".", 1)
        except ValueError:
            return None
        if not _VISITOR_PATTERN.fullmatch(visitor_id):
            return None
        expected = self._signed(visitor_id).rsplit(".", 1)[1]
        return visitor_id if hmac.compare_digest(signature, expected) else None

    def _opaque_identifier(self, namespace: str, value: str) -> str:
        return hmac.new(
            self._secret(), f"{namespace}:{value}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _secret() -> bytes:
        secret = settings.PUBLIC_VISITOR_SECRET or settings.JWT_SECRET
        return secret.encode("utf-8")


public_access_service = PublicAccessService()
