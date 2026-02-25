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
from sunny_scada.api.security import Principal
from sunny_scada.services.access_control_service import AccessControlService
from sunny_scada.services.system_config_service import SystemConfigService
from sunny_scada.services.watch_service import WatchService
from sunny_scada.db.models import AppClient, User


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


def get_access_control_service(request: Request) -> AccessControlService:
    return request.app.state.access_control_service


def get_system_config_service(request: Request, settings: Settings = Depends(get_settings)) -> SystemConfigService:
    return SystemConfigService(digital_bit_max=settings.digital_bit_max)


def get_watch_service(request: Request) -> WatchService:
    return request.app.state.watch_service


_bearer = HTTPBearer(auto_error=False)


def get_current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> Principal:
    # Preferred path: AuthEnforcementMiddleware already validated the request.
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal

    # Fallback path (e.g., tests/middleware disabled): validate here.
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = auth.decode_access_token_payload(creds.credentials)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid token")

    prt = str(payload.get("prt") or "user")
    if prt == "user":
        user_id = int(payload.get("sub"))
        user = db.query(User).filter(User.id == user_id).one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid token")
        perms = auth.user_permissions(db, user)
        return Principal(
            type="user",
            subject=str(user.id),
            user=user,
            username=user.username,
            permissions=perms,
            role_ids=[r.id for r in (user.roles or [])],
        )

    if prt == "app":
        client_id = str(payload.get("sub") or "").strip()
        client = db.query(AppClient).filter(AppClient.id == client_id).one_or_none()
        if not client or not client.is_active:
            raise HTTPException(status_code=401, detail="Invalid token")
        try:
            tok_ver = int(payload.get("ver") or 0)
        except Exception:
            tok_ver = -1
        if tok_ver != int(client.token_version or 0):
            raise HTTPException(status_code=401, detail="Invalid token")
        perms = auth.role_permissions(client.role)
        return Principal(
            type="app",
            subject=client.id,
            app_client=client,
            client_name=client.name,
            permissions=perms,
            role_ids=[client.role_id] if client.role_id else [],
        )

    raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    p = get_current_principal(request, creds, db, auth)
    if p.type != "user" or not p.user:
        raise HTTPException(status_code=403, detail="Forbidden")
    return p.user


def get_current_user_optional(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> User | None:
    if not creds or not creds.credentials:
        return None
    try:
        p = get_current_principal(request, creds, db, auth)
    except HTTPException:
        return None
    return p.user if p.type == "user" else None


def get_current_watch_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> Principal:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = auth.decode_access_token_payload(creds.credentials)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid token")

    if str(payload.get("scope") or "").strip().lower() != "watch":
        raise HTTPException(status_code=401, detail="Invalid token")

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal

    return get_current_principal(request, creds, db, auth)


def require_permission(permission: str):
    def _inner(
        principal: Principal = Depends(get_current_principal),
    ) -> bool:
        perms = principal.permissions
        if permission not in perms and permission.split(":", 1)[0] + ":*" not in perms:
            raise HTTPException(status_code=403, detail="Forbidden")
        return True

    return _inner


# -----------------
# RBAC (per PLC tree)
# -----------------

_ACL_PARAM_BY_TYPE = {
    "plc": "plc_id",
    "container": "container_id",
    "equipment": "equipment_id",
    "datapoint": "data_point_id",
}


def _is_admin_bypass(perms: set[str]) -> bool:
    # NOTE: config:write is intentionally *not* treated as an admin bypass here.
    # This keeps it possible to have roles that can write *some* resources while
    # still being restricted by ACL.
    return ("users:admin" in perms) or ("roles:admin" in perms)


def require_resource_read(resource_type: str):
    """Require read access for a specific resource instance.

    The resource id is pulled from path/query params based on resource_type.
    """

    resource_type = str(resource_type).strip().lower()
    param_name = _ACL_PARAM_BY_TYPE.get(resource_type, "resource_id")

    def _inner(
        request: Request,
        principal: Principal = Depends(get_current_principal),
        db: Session = Depends(get_db),
        auth: AuthService = Depends(get_auth_service),
        ac: AccessControlService = Depends(get_access_control_service),
    ) -> bool:
        perms = principal.permissions
        if _is_admin_bypass(perms):
            return True

        rid = request.path_params.get(param_name)
        if rid is None:
            rid = request.query_params.get(param_name)
        if rid is None:
            raise HTTPException(status_code=400, detail=f"Missing {param_name}")

        try:
            rid_int = int(rid)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid {param_name}")

        if principal.type == "user" and principal.user:
            ok = ac.can_read(db, principal.user, resource_type, rid_int)
        else:
            ok = ac.can_read_for_roles(db, role_ids=principal.role_ids, resource_type=resource_type, resource_id=rid_int)
        if not ok:
            raise HTTPException(status_code=403, detail="Forbidden")
        return True

    return _inner


def require_resource_write(resource_type: str):
    """Require write access for a specific resource instance."""

    resource_type = str(resource_type).strip().lower()
    param_name = _ACL_PARAM_BY_TYPE.get(resource_type, "resource_id")

    def _inner(
        request: Request,
        principal: Principal = Depends(get_current_principal),
        db: Session = Depends(get_db),
        auth: AuthService = Depends(get_auth_service),
        ac: AccessControlService = Depends(get_access_control_service),
    ) -> bool:
        perms = principal.permissions
        if _is_admin_bypass(perms):
            return True

        rid = request.path_params.get(param_name)
        if rid is None:
            rid = request.query_params.get(param_name)
        if rid is None:
            raise HTTPException(status_code=400, detail=f"Missing {param_name}")

        try:
            rid_int = int(rid)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid {param_name}")

        if principal.type == "user" and principal.user:
            ok = ac.can_write(db, principal.user, resource_type, rid_int)
        else:
            ok = ac.can_write_for_roles(db, role_ids=principal.role_ids, resource_type=resource_type, resource_id=rid_int)
        if not ok:
            raise HTTPException(status_code=403, detail="Forbidden")
        return True

    return _inner
