from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from sunny_scada.core.settings import Settings
from sunny_scada.data_storage import DataStorage
from sunny_scada.modbus_service import ModbusService
from sunny_scada.plc_reader import PLCReader
from sunny_scada.plc_writer import PLCWriter
from sunny_scada.services.alarm_service import AlarmService
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.auth_service import AuthService, InvalidToken
from sunny_scada.services.command_service import CommandService
from sunny_scada.services.config_service import ConfigService
from sunny_scada.services.data_points_service import DataPointsService
from sunny_scada.services.historian_service import HistorianService
from sunny_scada.services.iqf_service import IQFService
from sunny_scada.services.rate_limiter import RateLimiter
from sunny_scada.db.models import User


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> DataStorage:
    return request.app.state.storage


def get_modbus(request: Request) -> ModbusService:
    return request.app.state.modbus


def get_reader(request: Request) -> PLCReader:
    return request.app.state.plc_reader


def get_writer(request: Request) -> PLCWriter:
    return request.app.state.plc_writer


def get_data_points_service(request: Request) -> DataPointsService:
    return request.app.state.data_points_service


def get_alarm_service(request: Request) -> AlarmService:
    return request.app.state.alarm_service


def get_iqf_service(request: Request) -> IQFService:
    return request.app.state.iqf_service


# -----------------
# Database / Auth
# -----------------

def get_db(request: Request):
    SessionLocal = request.app.state.db_sessionmaker
    db: Session = SessionLocal()  # type: ignore
    try:
        yield db
    finally:
        db.close()


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_audit_service(request: Request) -> AuditService:
    return request.app.state.audit_service


def get_config_service(request: Request) -> ConfigService:
    return request.app.state.config_service


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_command_service(request: Request) -> CommandService:
    return request.app.state.command_service


def get_historian_service(request: Request) -> HistorianService:
    return request.app.state.historian_service


_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        user_id = auth.decode_access_token(creds.credentials)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def get_current_user_optional(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> User | None:
    if not creds or not creds.credentials:
        return None
    try:
        user_id = auth.decode_access_token(creds.credentials)
    except InvalidToken:
        return None
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user or not user.is_active:
        return None
    return user


def require_permission(permission: str):
    def _inner(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        auth: AuthService = Depends(get_auth_service),
    ) -> bool:
        perms = auth.user_permissions(db, user)
        if permission not in perms and permission.split(":", 1)[0] + ":*" not in perms:
            raise HTTPException(status_code=403, detail="Forbidden")
        return True

    return _inner
