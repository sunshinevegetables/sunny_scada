from __future__ import annotations

import datetime as dt
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_db,
    get_current_user,
    require_permission,
    get_auth_service,
    get_audit_service,
    get_access_control_service,
)
from sunny_scada.db.models import AppClient, Role, RolePermission, User
from sunny_scada.db.models import Alarm, AlarmOccurrence, AlarmEvent

router = APIRouter(prefix="/admin", tags=["admin"])


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=12, max_length=300)
    roles: list[str] = Field(default_factory=list)


@router.post("/users")
def create_user(
    req: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    auth=Depends(get_auth_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="username already exists")

    user = User(username=req.username, password_hash=auth.hash_password(req.password), is_active=True)
    if req.roles:
        roles = db.query(Role).filter(Role.name.in_(req.roles)).all()
        user.roles = roles

    db.add(user)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.user.create",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=req.username,
            metadata={"roles": req.roles},
        )
    except Exception:
        pass

    return {"id": user.id, "username": user.username, "is_active": user.is_active}


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("users:admin")),
):
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_active": u.is_active,
            "roles": [r.name for r in u.roles or []],
        }
        for u in users
    ]


class UserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=12, max_length=300)
    is_active: Optional[bool] = None
    roles: Optional[list[str]] = None


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    req: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    auth=Depends(get_auth_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if req.password:
        user.password_hash = auth.hash_password(req.password)
    if req.is_active is not None:
        user.is_active = bool(req.is_active)
    if req.roles is not None:
        roles = db.query(Role).filter(Role.name.in_(req.roles)).all() if req.roles else []
        user.roles = roles
    db.add(user)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.user.update",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=user.username,
            metadata={"roles": req.roles, "is_active": req.is_active, "password_changed": bool(req.password)},
        )
    except Exception:
        pass

    return {"id": user.id, "username": user.username, "is_active": user.is_active, "roles": [r.name for r in user.roles or []]}


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=12, max_length=300)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    req: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth=Depends(get_auth_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = auth.hash_password(req.password)
    db.add(user)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.user.reset_password",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=user.username,
            metadata={},
        )
    except Exception:
        pass

    return {"status": "ok"}



@router.post("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.add(user)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.user.deactivate",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=user.username,
            metadata={},
        )
    except Exception:
        pass

    return {"status": "ok"}


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)


@router.post("/roles")
def create_role(
    req: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    if db.query(Role).filter(Role.name == req.name).first():
        raise HTTPException(status_code=400, detail="role already exists")
    role = Role(name=req.name, description=req.description)
    db.add(role)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.role.create",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=req.name,
            metadata={},
        )
    except Exception:
        pass

    return {"id": role.id, "name": role.name}


@router.get("/roles")
def list_roles(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("roles:admin")),
):
    roles = db.query(Role).order_by(Role.id.asc()).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "permissions": [p.permission for p in r.permissions or []],
        }
        for r in roles
    ]


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)


@router.put("/roles/{role_id}")
def update_role(
    role_id: int,
    req: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    data = req.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        new_name = str(data["name"]).strip()
        if new_name and new_name != role.name:
            if db.query(Role).filter(Role.name == new_name).first():
                raise HTTPException(status_code=400, detail="role already exists")
            role.name = new_name
    if "description" in data:
        role.description = data.get("description")

    db.add(role)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.role.update",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=role.name,
            metadata={},
        )
    except Exception:
        pass

    return {"id": role.id, "name": role.name, "description": role.description}


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    name = role.name
    db.delete(role)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.role.delete",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=name,
            metadata={},
        )
    except Exception:
        pass

    return {"status": "ok"}


class PermissionRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=200)


class AccessGrantUpsert(BaseModel):
    resource_type: str = Field(pattern=r"^(plc|container|equipment|datapoint)$")
    resource_id: int = Field(ge=1)
    access_level: str = Field(pattern=r"^(read|write)$")
    include_descendants: bool = True


class AccessGrantOut(BaseModel):
    id: int
    role_id: int | None
    user_id: int | None
    resource_type: str
    resource_id: int
    access_level: str
    include_descendants: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by_user_id: int | None


def _grant_out(g) -> AccessGrantOut:
    return AccessGrantOut(
        id=g.id,
        role_id=g.role_id,
        user_id=g.user_id,
        resource_type=g.resource_type,
        resource_id=g.resource_id,
        access_level=g.access_level,
        include_descendants=bool(g.include_descendants),
        created_at=g.created_at,
        updated_at=g.updated_at,
        created_by_user_id=g.created_by_user_id,
    )


@router.get("/roles/{role_id}/permissions")
def list_role_permissions(
    role_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return {"role": role.name, "permissions": [p.permission for p in role.permissions or []]}


@router.get("/alarms")
def list_alarms(
    limit: int = 200,
    offset: int = 0,
    severity: Optional[str] = None,
    acked: Optional[bool] = None,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
):
    from sqlalchemy import union_all, select, cast, String, literal
    from sqlalchemy.sql import func
    
    # Legacy Alarm table query
    s1 = select(
        Alarm.alarm_id.label("alarm_id"),
        Alarm.ts.label("ts"),
        Alarm.severity.label("severity"),
        Alarm.message.label("message"),
        Alarm.source.label("source"),
        Alarm.meta.label("meta"),
        Alarm.acked.label("acked"),
        Alarm.acked_at.label("acked_at"),
        Alarm.acked_by_user_id.label("acked_by_user_id"),
        Alarm.acked_by_client_ip.label("acked_by_client_ip"),
    )
    if severity:
        s1 = s1.where(Alarm.severity == str(severity))
    if acked is not None:
        s1 = s1.where(Alarm.acked == bool(acked))
    
    # New unified alarm (AlarmEvent + AlarmOccurrence) query
    s2 = select(
        (literal("ev_") + cast(AlarmEvent.id, String)).label("alarm_id"),
        AlarmEvent.ts.label("ts"),
        AlarmEvent.severity.label("severity"),
        AlarmEvent.message.label("message"),
        AlarmEvent.source.label("source"),
        AlarmEvent.meta.label("meta"),
        AlarmOccurrence.acknowledged.label("acked"),
        AlarmOccurrence.acknowledged_at.label("acked_at"),
        AlarmOccurrence.acknowledged_by_user_id.label("acked_by_user_id"),
        AlarmOccurrence.acknowledged_by_client_ip.label("acked_by_client_ip"),
    ).select_from(AlarmEvent).join(
        AlarmOccurrence, AlarmEvent.occurrence_id == AlarmOccurrence.id
    )
    if severity:
        s2 = s2.where(AlarmEvent.severity == str(severity))
    if acked is not None:
        s2 = s2.where(AlarmOccurrence.acknowledged == bool(acked))
    
    # Union both queries
    u = union_all(s1, s2).subquery()
    total = db.execute(select(func.count()).select_from(u)).scalar() or 0
    rows = db.execute(
        select(u).order_by(u.c.ts.desc()).offset(int(offset)).limit(int(limit))
    ).all()
    
    out = []
    for r in rows:
        acked_by_user = None
        if r.acked_by_user_id:
            user = db.query(User).filter(User.id == r.acked_by_user_id).one_or_none()
            acked_by_user = user.username if user else None
        
        out.append(
            {
                "alarm_id": r.alarm_id,
                "ts": r.ts,
                "severity": r.severity or "info",
                "message": r.message or "",
                "source": r.source or "",
                "meta": r.meta or {},
                "acked": bool(r.acked),
                "acked_at": r.acked_at,
                "acked_by_user": acked_by_user,
                "acked_by_client_ip": r.acked_by_client_ip,
            }
        )
    
    return {"total": total, "items": out}


@router.post("/roles/{role_id}/permissions")
def add_role_permission(
    role_id: int,
    req: PermissionRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if any(rp.permission == req.permission for rp in role.permissions or []):
        return {"status": "exists"}
    role.permissions.append(RolePermission(role_id=role.id, permission=req.permission))
    db.add(role)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.role.permission.add",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=role.name,
            metadata={"permission": req.permission},
        )
    except Exception:
        pass

    return {"status": "ok"}


@router.delete("/roles/{role_id}/permissions/{perm}")
def remove_role_permission(
    role_id: int,
    perm: str,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    before = len(role.permissions or [])
    role.permissions = [rp for rp in role.permissions or [] if rp.permission != perm]
    db.add(role)
    db.commit()

    if len(role.permissions or []) != before:
        try:
            audit.log(
                db,
                action="admin.role.permission.remove",
                user_id=me.id,
                client_ip=request.client.host if request.client else None,
                resource=role.name,
                metadata={"permission": perm},
            )
        except Exception:
            pass

    return {"status": "ok"}


# -----------------
# RBAC grants admin
# -----------------


@router.get("/access/roles/{role_id}/grants", response_model=list[AccessGrantOut])
def list_role_access_grants(
    role_id: int,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    grants = ac.list_role_grants(db, role_id=role_id)
    return [_grant_out(g) for g in grants]


@router.put("/access/roles/{role_id}/grants", response_model=AccessGrantOut)
def upsert_role_access_grant(
    role_id: int,
    req: AccessGrantUpsert,
    request: Request,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    try:
        g = ac.upsert_grant(
            db,
            role_id=role_id,
            resource_type=req.resource_type,
            resource_id=req.resource_id,
            access_level=req.access_level,
            include_descendants=req.include_descendants,
            created_by_user_id=me.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="admin.access.role.grant.upsert",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=role.name,
            metadata={
                "grant_id": g.id,
                "resource_type": g.resource_type,
                "resource_id": g.resource_id,
                "access_level": g.access_level,
                "include_descendants": bool(g.include_descendants),
            },
        )
    except Exception:
        pass

    return _grant_out(g)


@router.delete("/access/roles/{role_id}/grants/{grant_id}")
def delete_role_access_grant(
    role_id: int,
    grant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    try:
        ac.delete_grant(db, grant_id=grant_id, role_id=role_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        audit.log(
            db,
            action="admin.access.role.grant.delete",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=role.name,
            metadata={"grant_id": int(grant_id)},
        )
    except Exception:
        pass

    return {"status": "ok"}


@router.delete("/access/roles/{role_id}/grants")
def clear_role_access_grants(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("roles:admin")),
):
    role = db.query(Role).filter(Role.id == role_id).one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    deleted = ac.clear_role_grants(db, role_id=role_id)

    try:
        audit.log(
            db,
            action="admin.access.role.grant.clear",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=role.name,
            metadata={"deleted": deleted},
        )
    except Exception:
        pass

    return {"status": "ok", "deleted": deleted}


@router.get("/access/users/{user_id}/grants", response_model=list[AccessGrantOut])
def list_user_access_grants(
    user_id: int,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    grants = ac.list_user_grants(db, user_id=user_id)
    return [_grant_out(g) for g in grants]


@router.put("/access/users/{user_id}/grants", response_model=AccessGrantOut)
def upsert_user_access_grant(
    user_id: int,
    req: AccessGrantUpsert,
    request: Request,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        g = ac.upsert_grant(
            db,
            user_id=user_id,
            resource_type=req.resource_type,
            resource_id=req.resource_id,
            access_level=req.access_level,
            include_descendants=req.include_descendants,
            created_by_user_id=me.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="admin.access.user.grant.upsert",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=user.username,
            metadata={
                "grant_id": g.id,
                "resource_type": g.resource_type,
                "resource_id": g.resource_id,
                "access_level": g.access_level,
                "include_descendants": bool(g.include_descendants),
            },
        )
    except Exception:
        pass

    return _grant_out(g)


@router.delete("/access/users/{user_id}/grants/{grant_id}")
def delete_user_access_grant(
    user_id: int,
    grant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        ac.delete_grant(db, grant_id=grant_id, user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        audit.log(
            db,
            action="admin.access.user.grant.delete",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=user.username,
            metadata={"grant_id": int(grant_id)},
        )
    except Exception:
        pass

    return {"status": "ok"}


@router.delete("/access/users/{user_id}/grants")
def clear_user_access_grants(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ac=Depends(get_access_control_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    deleted = ac.clear_user_grants(db, user_id=user_id)

    try:
        audit.log(
            db,
            action="admin.access.user.grant.clear",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=user.username,
            metadata={"deleted": deleted},
        )
    except Exception:
        pass

    return {"status": "ok", "deleted": deleted}


# -----------------
# App Clients (service-to-service)
# -----------------


class AppClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: Optional[str] = Field(default=None, max_length=100)
    allowed_ips: list[str] = Field(default_factory=list)


class AppClientUpdate(BaseModel):
    role: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None
    allowed_ips: Optional[list[str]] = None


@router.post("/app-clients")
def create_app_client(
    req: AppClientCreate,
    request: Request,
    db: Session = Depends(get_db),
    auth=Depends(get_auth_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    if db.query(AppClient).filter(AppClient.name == req.name).first():
        raise HTTPException(status_code=400, detail="App client name already exists")

    role_id = None
    role_name = None
    if req.role:
        role = db.query(Role).filter(Role.name == req.role).one_or_none()
        if not role:
            raise HTTPException(status_code=400, detail="Unknown role")
        role_id = role.id
        role_name = role.name

    client_secret = secrets.token_urlsafe(48)
    client = AppClient(
        name=req.name,
        role_id=role_id,
        secret_hash=auth.hash_password(client_secret),
        is_active=True,
        allowed_ips=list(req.allowed_ips or []),
        token_version=0,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    try:
        audit.log(
            db,
            action="admin.app_client.create",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=client.id,
            metadata={"name": client.name, "role": role_name, "allowed_ips": req.allowed_ips},
        )
    except Exception:
        pass

    # Return secret only once.
    return {
        "id": client.id,
        "name": client.name,
        "role": role_name,
        "is_active": client.is_active,
        "allowed_ips": client.allowed_ips,
        "token_version": client.token_version,
        "client_secret": client_secret,
    }


@router.get("/app-clients")
def list_app_clients(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("users:admin")),
):
    rows = db.query(AppClient).order_by(AppClient.created_at.desc()).all()
    out = []
    for c in rows:
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "role_id": c.role_id,
                "role": c.role.name if c.role else None,
                "is_active": c.is_active,
                "allowed_ips": c.allowed_ips or [],
                "token_version": c.token_version,
                "created_at": c.created_at,
                "last_used_at": c.last_used_at,
            }
        )
    return out


@router.put("/app-clients/{client_id}")
def update_app_client(
    client_id: str,
    req: AppClientUpdate,
    request: Request,
    db: Session = Depends(get_db),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    client = db.query(AppClient).filter(AppClient.id == client_id).one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="App client not found")

    bumped = False
    if req.role is not None:
        if req.role == "":
            if client.role_id is not None:
                client.role_id = None
                bumped = True
        else:
            role = db.query(Role).filter(Role.name == req.role).one_or_none()
            if not role:
                raise HTTPException(status_code=400, detail="Unknown role")
            if client.role_id != role.id:
                client.role_id = role.id
                bumped = True

    if req.allowed_ips is not None:
        client.allowed_ips = list(req.allowed_ips or [])
        bumped = True

    if req.is_active is not None and bool(req.is_active) != bool(client.is_active):
        client.is_active = bool(req.is_active)
        bumped = True

    if bumped:
        client.token_version = int(client.token_version or 0) + 1

    db.add(client)
    db.commit()
    db.refresh(client)

    try:
        audit.log(
            db,
            action="admin.app_client.update",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=client.id,
            metadata={"role_id": client.role_id, "is_active": client.is_active, "allowed_ips": client.allowed_ips},
        )
    except Exception:
        pass

    return {
        "id": client.id,
        "name": client.name,
        "role": client.role.name if client.role else None,
        "is_active": client.is_active,
        "allowed_ips": client.allowed_ips or [],
        "token_version": client.token_version,
        "created_at": client.created_at,
        "last_used_at": client.last_used_at,
    }


@router.post("/app-clients/{client_id}/rotate-secret")
def rotate_app_client_secret(
    client_id: str,
    request: Request,
    db: Session = Depends(get_db),
    auth=Depends(get_auth_service),
    audit=Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("users:admin")),
):
    client = db.query(AppClient).filter(AppClient.id == client_id).one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="App client not found")

    new_secret = secrets.token_urlsafe(48)
    client.secret_hash = auth.hash_password(new_secret)
    client.token_version = int(client.token_version or 0) + 1
    db.add(client)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.app_client.rotate_secret",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=client.id,
            metadata={"token_version": client.token_version},
        )
    except Exception:
        pass

    # Return secret only at rotation time.
    return {"id": client.id, "client_secret": new_secret, "token_version": client.token_version}
