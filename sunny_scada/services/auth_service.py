from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from sunny_scada.db.models import RefreshToken, Role, RolePermission, User


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: dt.datetime
    refresh_expires_at: dt.datetime


class AuthError(RuntimeError):
    pass


class InvalidCredentials(AuthError):
    pass


class UserLocked(AuthError):
    def __init__(self, until: dt.datetime):
        super().__init__(f"User is locked until {until.isoformat()}")
        self.until = until


class InvalidToken(AuthError):
    pass


class AuthService:
    """Authentication + RBAC helper.

    Cycle 1: login/refresh/me/logout. Admin CRUD is deferred to Cycle 2.
    """

    def __init__(
        self,
        *,
        jwt_secret_key: str,
        jwt_issuer: str = "sunny_scada",
        access_ttl_s: int = 900,
        refresh_ttl_s: int = 60 * 60 * 24 * 7,
        lockout_threshold: int = 5,
        lockout_duration_s: int = 900,
    ) -> None:
        if not jwt_secret_key:
            raise ValueError("JWT secret key must be provided via env var JWT_SECRET_KEY")

        self._jwt_secret_key = jwt_secret_key
        self._jwt_issuer = jwt_issuer
        self._access_ttl_s = int(access_ttl_s)
        self._refresh_ttl_s = int(refresh_ttl_s)
        self._lockout_threshold = int(lockout_threshold)
        self._lockout_duration_s = int(lockout_duration_s)

        # Argon2 parameters are already sane in argon2-cffi defaults.
        self._hasher = PasswordHasher()

    def hash_password(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False

    @staticmethod
    def _sha256_hex(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def ensure_initial_admin(
        self,
        db: Session,
        *,
        username: str,
        password: str,
        permissions: Sequence[str],
    ) -> None:
        """Create an initial admin user/role if DB is empty."""
        if db.query(User).count() > 0:
            return

        admin_role = Role(name="admin", description="Initial admin role")
        admin_role.permissions = [RolePermission(permission=p) for p in permissions]
        admin_user = User(username=username, password_hash=self.hash_password(password), is_active=True)
        admin_user.roles = [admin_role]

        db.add(admin_role)
        db.add(admin_user)
        db.commit()

    def authenticate(self, db: Session, *, username: str, password: str) -> TokenPair:
        user = db.query(User).filter(User.username == username).one_or_none()
        if not user or not user.is_active:
            # Intentionally ambiguous (don't reveal existence)
            raise InvalidCredentials("Invalid username or password")

        now = dt.datetime.now(dt.timezone.utc)
        if user.locked_until and user.locked_until > now:
            raise UserLocked(user.locked_until)

        if not self.verify_password(password, user.password_hash):
            user.failed_login_count = int(user.failed_login_count or 0) + 1
            if user.failed_login_count >= self._lockout_threshold:
                user.locked_until = now + dt.timedelta(seconds=self._lockout_duration_s)
                user.failed_login_count = 0
            db.add(user)
            db.commit()
            raise InvalidCredentials("Invalid username or password")

        # Success: clear counters
        user.failed_login_count = 0
        user.locked_until = None
        db.add(user)
        db.commit()

        return self._issue_tokens(db, user)

    def _issue_tokens(self, db: Session, user: User) -> TokenPair:
        now = dt.datetime.now(dt.timezone.utc)

        access_expires = now + dt.timedelta(seconds=self._access_ttl_s)
        refresh_expires = now + dt.timedelta(seconds=self._refresh_ttl_s)

        payload = {
            "iss": self._jwt_issuer,
            "sub": str(user.id),
            "username": user.username,
            "iat": int(now.timestamp()),
            "exp": int(access_expires.timestamp()),
        }

        access_token = jwt.encode(payload, self._jwt_secret_key, algorithm="HS256")

        refresh_token = secrets.token_urlsafe(48)
        rt = RefreshToken(
            user_id=user.id,
            token_sha256=self._sha256_hex(refresh_token),
            revoked=False,
            expires_at=refresh_expires,
        )
        db.add(rt)
        db.commit()

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=access_expires,
            refresh_expires_at=refresh_expires,
        )

    def refresh(self, db: Session, *, refresh_token: str) -> TokenPair:
        now = dt.datetime.now(dt.timezone.utc)
        token_hash = self._sha256_hex(refresh_token)

        rt = (
            db.query(RefreshToken)
            .filter(RefreshToken.token_sha256 == token_hash)
            .one_or_none()
        )
        if not rt or rt.revoked or rt.expires_at <= now:
            raise InvalidToken("Invalid or expired refresh token")

        user = db.query(User).filter(User.id == rt.user_id).one_or_none()
        if not user or not user.is_active:
            raise InvalidToken("User not active")

        # Rotate refresh token
        rt.revoked = True
        db.add(rt)
        db.commit()

        return self._issue_tokens(db, user)

    def logout(self, db: Session, *, refresh_token: Optional[str]) -> None:
        if not refresh_token:
            return
        token_hash = self._sha256_hex(refresh_token)
        rt = db.query(RefreshToken).filter(RefreshToken.token_sha256 == token_hash).one_or_none()
        if not rt:
            return
        rt.revoked = True
        db.add(rt)
        db.commit()

    def decode_access_token(self, token: str) -> int:
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret_key,
                algorithms=["HS256"],
                issuer=self._jwt_issuer,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except Exception as e:
            raise InvalidToken("Invalid access token") from e

        sub = payload.get("sub")
        try:
            return int(sub)
        except Exception as e:
            raise InvalidToken("Invalid access token subject") from e

    @staticmethod
    def expand_permissions(perms: Iterable[str]) -> set[str]:
        """Support wildcard permissions like alarms:*"""
        expanded: set[str] = set()
        for p in perms:
            p = (p or "").strip()
            if not p:
                continue
            expanded.add(p)
            if p.endswith(":*"):
                expanded.add(p.split(":", 1)[0] + ":read")
                expanded.add(p.split(":", 1)[0] + ":write")
        return expanded

    def user_permissions(self, db: Session, user: User) -> set[str]:
        perms: set[str] = set()
        for role in user.roles or []:
            for rp in role.permissions or []:
                perms.add(rp.permission)
        return self.expand_permissions(perms)
