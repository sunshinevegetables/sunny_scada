from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_user, require_permission, get_auth_service, get_audit_service
from sunny_scada.db.models import Role, RolePermission, User

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
    role.description = req.description
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
