from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_config_service,
    get_db,
    get_current_user,
    require_permission,
    get_audit_service,
)
from sunny_scada.db.models import ConfigRevision
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.config_service import ConfigService, ConfigError, NotFound, Conflict


router = APIRouter(prefix="/config", tags=["config-admin"])


# -----------------
# Schemas
# -----------------
class PLCCreateRequest(BaseModel):
    plc_id: str = Field(min_length=1)
    content: Dict[str, Any] | None = None


class PLCUpdateRequest(BaseModel):
    new_id: str | None = None
    content: Dict[str, Any] | None = None


class DatapointCreateRequest(BaseModel):
    datapoint_id: str = Field(min_length=1)
    direction: str = Field(pattern="^(read|write)$")
    parent_path: str = Field(default="")
    data: Dict[str, Any]


class DatapointUpdateRequest(BaseModel):
    data: Dict[str, Any]
    path: str | None = None
    direction: str | None = Field(default=None, pattern="^(read|write)$")


class ParametersPatchRequest(BaseModel):
    set: Dict[str, Any] = Field(default_factory=dict)
    delete: List[str] = Field(default_factory=list)
    path: str | None = None
    direction: str | None = Field(default=None, pattern="^(read|write)$")


def _client_ip(request: Request) -> str | None:
    # NOTE: In Cycle 2 we can add TRUST_PROXY_HEADERS setting.
    if request.client:
        return request.client.host
    return None


def _record_revision(
    db: Session,
    *,
    action: str,
    yaml_path: str,
    before_yaml: str,
    after_yaml: str,
    diff: str,
    user_id: int | None,
    client_ip: str | None,
    backup_path: str | None = None,
) -> ConfigRevision:
    rev = ConfigRevision(
        action=action,
        yaml_path=yaml_path,
        before_yaml=before_yaml,
        after_yaml=after_yaml,
        diff=diff,
        user_id=user_id,
        client_ip=client_ip,
        backup_path=backup_path,
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)
    return rev


# -----------------
# PLC CRUD
# -----------------
@router.get("/plcs")
def list_plcs(
    _=Depends(require_permission("config:read")),
    svc: ConfigService = Depends(get_config_service),
):
    return {"plcs": svc.list_plcs()}


@router.post("/plcs")
def create_plc(
    req: PLCCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.create_plc(req.plc_id, content=req.content)
    except Conflict as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(e))
    except ConfigError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))

    rev = _record_revision(
        db,
        action=f"create_plc:{req.plc_id}",
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.create_plc",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=req.plc_id,
        metadata={"revision_id": rev.id},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


@router.get("/plcs/{plc_id}")
def get_plc(
    plc_id: str,
    _=Depends(require_permission("config:read")),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        plc = svc.get_plc(plc_id)
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
    return {"plc_id": plc_id, "data": plc}


@router.put("/plcs/{plc_id}")
def update_plc(
    plc_id: str,
    req: PLCUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.update_plc(plc_id, new_id=req.new_id, content=req.content)
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(e))
    except ConfigError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))

    action = f"update_plc:{plc_id}" if not req.new_id else f"rename_plc:{plc_id}->{req.new_id}"
    rev = _record_revision(
        db,
        action=action,
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.update_plc",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=plc_id,
        metadata={"revision_id": rev.id, "new_id": req.new_id},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


@router.delete("/plcs/{plc_id}")
def delete_plc(
    plc_id: str,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.delete_plc(plc_id)
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))

    rev = _record_revision(
        db,
        action=f"delete_plc:{plc_id}",
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.delete_plc",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=plc_id,
        metadata={"revision_id": rev.id},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


# -----------------
# Datapoint CRUD
# -----------------
@router.get("/plcs/{plc_id}/datapoints")
def list_datapoints(
    plc_id: str,
    _=Depends(require_permission("config:read")),
    svc: ConfigService = Depends(get_config_service),
):
    return {"plc_id": plc_id, "datapoints": svc.list_datapoints(plc_id)}


@router.post("/plcs/{plc_id}/datapoints")
def create_datapoint(
    plc_id: str,
    req: DatapointCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.create_datapoint(
            plc_id,
            dp_id=req.datapoint_id,
            direction=req.direction,
            parent_path=req.parent_path,
            data=req.data,
        )
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(e))
    except ConfigError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))

    rev = _record_revision(
        db,
        action=f"create_datapoint:{plc_id}:{req.datapoint_id}",
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.create_datapoint",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=f"{plc_id}:{req.datapoint_id}",
        metadata={"revision_id": rev.id, "direction": req.direction, "parent_path": req.parent_path},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


@router.get("/plcs/{plc_id}/datapoints/{dp_id}")
def get_datapoint(
    plc_id: str,
    dp_id: str,
    path: str | None = None,
    direction: str | None = None,
    _=Depends(require_permission("config:read")),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        with svc._file_lock():  # intentional: reuse lock for consistent read
            svc_root = svc.load()
            matches = svc.find_datapoint(plc_id, dp_id, path=path, direction=direction)
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))

    if not matches:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
    if len(matches) > 1:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=f"Ambiguous datapoint. Provide 'path' or 'direction'. Matches: {[ '/'.join(p) for p,_ in matches ]}")

    dp_path, dp = matches[0]
    return {"plc_id": plc_id, "datapoint_id": dp_id, "path": "/".join(dp_path[:-1]), "data": dp}


@router.put("/plcs/{plc_id}/datapoints/{dp_id}")
def update_datapoint(
    plc_id: str,
    dp_id: str,
    req: DatapointUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.update_datapoint(plc_id, dp_id, data=req.data, path=req.path, direction=req.direction)
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(e))
    except ConfigError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))

    rev = _record_revision(
        db,
        action=f"update_datapoint:{plc_id}:{dp_id}",
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.update_datapoint",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=f"{plc_id}:{dp_id}",
        metadata={"revision_id": rev.id},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


@router.delete("/plcs/{plc_id}/datapoints/{dp_id}")
def delete_datapoint(
    plc_id: str,
    dp_id: str,
    request: Request,
    path: str | None = None,
    direction: str | None = None,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.delete_datapoint(plc_id, dp_id, path=path, direction=direction)
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(e))
    except ConfigError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))

    rev = _record_revision(
        db,
        action=f"delete_datapoint:{plc_id}:{dp_id}",
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.delete_datapoint",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=f"{plc_id}:{dp_id}",
        metadata={"revision_id": rev.id},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


@router.patch("/plcs/{plc_id}/datapoints/{dp_id}/parameters")
def patch_parameters(
    plc_id: str,
    dp_id: str,
    req: ParametersPatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    try:
        before, after, diff = svc.patch_datapoint_parameters(
            plc_id,
            dp_id,
            set_params=req.set,
            delete_params=req.delete,
            path=req.path,
            direction=req.direction,
        )
    except NotFound as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=str(e))
    except ConfigError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(e))

    rev = _record_revision(
        db,
        action=f"patch_parameters:{plc_id}:{dp_id}",
        yaml_path=str(svc.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.patch_parameters",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=f"{plc_id}:{dp_id}",
        metadata={"revision_id": rev.id, "set": list(req.set.keys()), "delete": req.delete},
        config_revision_id=rev.id,
    )
    return {"status": "ok", "revision_id": rev.id}


# -----------------
# Validate / Download / Revisions / Rollback
# -----------------
@router.post("/validate")
def validate_config(
    _=Depends(require_permission("config:read")),
    svc: ConfigService = Depends(get_config_service),
):
    return svc.validate()


@router.get("/download")
def download_config(
    _=Depends(require_permission("config:read")),
    svc: ConfigService = Depends(get_config_service),
):
    return FileResponse(str(svc.path), filename=svc.path.name)


@router.get("/revisions")
def list_revisions(
    db: Session = Depends(get_db),
    _=Depends(require_permission("config:read")),
):
    rows = (
        db.query(ConfigRevision)
        .order_by(ConfigRevision.id.desc())
        .limit(200)
        .all()
    )
    return {
        "revisions": [
            {
                "id": r.id,
                "ts": r.ts,
                "user_id": r.user_id,
                "client_ip": r.client_ip,
                "action": r.action,
                "yaml_path": r.yaml_path,
            }
            for r in rows
        ]
    }


@router.post("/rollback/{revision_id}")
def rollback(
    revision_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    svc: ConfigService = Depends(get_config_service),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
):
    rev = db.query(ConfigRevision).filter(ConfigRevision.id == revision_id).one_or_none()
    if not rev:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Revision not found")

    # Rollback to before_yaml snapshot
    with svc._file_lock():
        before_current = svc.read_snapshot()
        # Load snapshot through ruamel to keep formatting consistent
        from ruamel.yaml import YAML
        from io import StringIO

        y = YAML(typ="rt")
        data = y.load(StringIO(rev.before_yaml))
        svc.atomic_write(data)
        after = svc.read_snapshot()
        diff = svc.compute_diff(before_current, after)

    new_rev = _record_revision(
        db,
        action=f"rollback_to:{revision_id}",
        yaml_path=str(svc.path),
        before_yaml=before_current,
        after_yaml=after,
        diff=diff,
        user_id=user.id,
        client_ip=_client_ip(request),
    )
    audit.log(
        db,
        action="config.rollback",
        user_id=user.id,
        client_ip=_client_ip(request),
        resource=str(revision_id),
        metadata={"rolled_back_to": revision_id, "new_revision_id": new_rev.id},
        config_revision_id=new_rev.id,
    )
    return {"status": "ok", "revision_id": new_rev.id}
