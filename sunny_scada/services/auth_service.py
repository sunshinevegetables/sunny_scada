from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from sunny_scada.db.models import RefreshToken, Role, RolePermission, User


def _to_aware(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    if t.tzinfo is None:
        return t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


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
        jwt_audience: str = "",
        jwt_leeway_s: int = 30,
        access_ttl_s: int = 900,
        app_access_ttl_s: int = 3600,
        refresh_ttl_s: int = 60 * 60 * 24 * 7,
        lockout_threshold: int = 5,
        lockout_duration_s: int = 900,
    ) -> None:
        if not jwt_secret_key:
            raise ValueError("JWT secret key must be provided via env var JWT_SECRET_KEY")

        self._jwt_secret_key = jwt_secret_key
        self._jwt_issuer = jwt_issuer
        self._jwt_audience = (jwt_audience or "").strip()
        self._jwt_leeway_s = max(0, int(jwt_leeway_s))
        self._access_ttl_s = int(access_ttl_s)
        self._app_access_ttl_s = int(app_access_ttl_s)
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
        locked_until = _to_aware(user.locked_until)
        if locked_until and locked_until > now:
            raise UserLocked(locked_until)

        if not self.verify_password(password, user.password_hash):
            user.failed_login_count = int(user.failed_login_count or 0) + 1
            if user.failed_login_count >= self._lockout_threshold:
                until = now + dt.timedelta(seconds=self._lockout_duration_s)
                user.locked_until = until
                user.failed_login_count = 0
            db.add(user)
            db.commit()
            # If we just locked the account, surface a different error for audit + UI.
            new_locked_until = _to_aware(user.locked_until)
            if new_locked_until and new_locked_until > now:
                raise UserLocked(new_locked_until)
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

        payload: dict[str, Any] = {
            "iss": self._jwt_issuer,
            "aud": self._jwt_audience if self._jwt_audience else None,
            "sub": str(user.id),
            "prt": "user",
            "typ": "access",
            "jti": secrets.token_urlsafe(16),
            "username": user.username,
            "iat": int(now.timestamp()),
            "exp": int(access_expires.timestamp()),
        }

        # Drop aud if not configured so older clients remain compatible.
        if payload.get("aud") is None:
            payload.pop("aud", None)

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
        rt_expires = _to_aware(rt.expires_at) if rt else None
        if not rt or rt.revoked or (rt_expires is not None and rt_expires <= now):
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
        payload = self.decode_access_token_payload(token)
        # Backward compatible: if prt missing, assume user
        prt = str(payload.get("prt") or "user")
        if prt != "user":
            raise InvalidToken("Invalid access token")
        sub = payload.get("sub")
        try:
            return int(sub)
        except Exception as e:
            raise InvalidToken("Invalid access token subject") from e

    def decode_access_token_payload(self, token: str) -> dict[str, Any]:
        """Decode and validate an access JWT.

        Returns the decoded payload dict.
        """
        try:
            kwargs: dict[str, Any] = {
                "key": self._jwt_secret_key,
                "algorithms": ["HS256"],
                "issuer": self._jwt_issuer,
                "options": {"require": ["exp", "iat", "iss", "sub"]},
                "leeway": self._jwt_leeway_s,
            }
            if self._jwt_audience:
                kwargs["audience"] = self._jwt_audience
            payload = jwt.decode(token, **kwargs)
        except Exception as e:
            raise InvalidToken("Invalid access token") from e

        # Backward compatible for older tokens that may not include typ/prt.
        typ = str(payload.get("typ") or "access")
        if typ != "access":
            raise InvalidToken("Invalid access token")
        prt = str(payload.get("prt") or "user")
        if prt not in ("user", "app"):
            raise InvalidToken("Invalid access token")
        return payload

    def issue_app_access_token(
        self,
        *,
        client_id: str,
        client_name: str,
        role_id: int | None,
        token_version: int,
        ttl_s: int | None = None,
    ) -> tuple[str, dt.datetime]:
        now = dt.datetime.now(dt.timezone.utc)
        access_expires = now + dt.timedelta(seconds=int(ttl_s or self._app_access_ttl_s))
        payload: dict[str, Any] = {
            "iss": self._jwt_issuer,
            "aud": self._jwt_audience if self._jwt_audience else None,
            "sub": str(client_id),
            "prt": "app",
            "typ": "access",
            "jti": secrets.token_urlsafe(16),
            "client_name": str(client_name),
            "role_id": int(role_id) if role_id is not None else None,
            "ver": int(token_version),
            "iat": int(now.timestamp()),
            "exp": int(access_expires.timestamp()),
        }
        if payload.get("aud") is None:
            payload.pop("aud", None)
        if payload.get("role_id") is None:
            payload.pop("role_id", None)
        token = jwt.encode(payload, self._jwt_secret_key, algorithm="HS256")
        return token, access_expires

    def issue_user_access_token(
        self,
        *,
        user: User,
        ttl_s: int | None = None,
        scope: str = "",
    ) -> tuple[str, dt.datetime]:
        now = dt.datetime.now(dt.timezone.utc)
        access_expires = now + dt.timedelta(seconds=int(ttl_s or self._access_ttl_s))
        payload: dict[str, Any] = {
            "iss": self._jwt_issuer,
            "aud": self._jwt_audience if self._jwt_audience else None,
            "sub": str(user.id),
            "prt": "user",
            "typ": "access",
            "jti": secrets.token_urlsafe(16),
            "username": user.username,
            "scope": str(scope or "").strip(),
            "iat": int(now.timestamp()),
            "exp": int(access_expires.timestamp()),
        }
        if payload.get("aud") is None:
            payload.pop("aud", None)
        if not payload.get("scope"):
            payload.pop("scope", None)
        token = jwt.encode(payload, self._jwt_secret_key, algorithm="HS256")
        return token, access_expires

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

    def role_permissions(self, role: Role | None) -> set[str]:
        if not role:
            return set()
        perms: set[str] = set()
        for rp in role.permissions or []:
            perms.add(rp.permission)
        return self.expand_permissions(perms)
