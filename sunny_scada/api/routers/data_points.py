from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_data_points_service,
    get_config_service,
    get_db,
    get_current_principal,
    require_permission,
    get_audit_service,
)
from sunny_scada.api.security import Principal
from sunny_scada.api.schemas import UpdateDataPointRequest
from sunny_scada.services.data_points_service import DataPointsService
from sunny_scada.services.config_service import ConfigService
from sunny_scada.services.audit_service import AuditService
from sunny_scada.db.models import ConfigRevision

router = APIRouter(tags=["data-points"])


@router.get("/get_data_point", summary="Get Data Point", description="Fetch a specific data point from the data_points.yaml file.")
def get_data_point(
    path: str,
    svc: DataPointsService = Depends(get_data_points_service),
    _perm=Depends(require_permission("config:read")),
):
    data = svc.get_by_path(path)
    if data is None:
        raise HTTPException(status_code=404, detail="Data point not found.")
    return data


@router.post("/update_data_point", summary="Update Data Point", description="Update an existing data point in the YAML file.")
def update_data_point(
    req: UpdateDataPointRequest,
    request: Request,
    config: ConfigService = Depends(get_config_service),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(get_current_principal),
    _perm=Depends(require_permission("config:write")),
):
    # Backward compatible behavior, but now with file lock + atomic write.
    value = {
        "type": req.type,
        "description": req.description,
        "address": req.address,
        **({"bits": req.bits} if req.type == "DIGITAL" and req.bits else {}),
    }
    try:
        before, after, diff = config.update_leaf_by_absolute_path(req.path, value)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Path not found or update failed: {e}")

    rev = ConfigRevision(
        action=f"legacy_update_data_point:{req.name}",
        yaml_path=str(config.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=principal.user.id if principal.user else None,
        client_ip=request.client.host if request.client else None,
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)
    audit.log(
        db,
        action="config.legacy_update_data_point",
        user_id=principal.user.id if principal.user else None,
        client_ip=request.client.host if request.client else None,
        resource=req.name,
        metadata={"actor": principal.actor_key, "path": req.path, "revision_id": rev.id},
        config_revision_id=rev.id,
    )
    return {"message": f"Data point '{req.name}' updated successfully at {req.path}.", "revision_id": rev.id}


@router.post("/add_data_point", summary="Add Data Point", description="Add a data point into the YAML file dynamically.")
def add_data_point(
    req: UpdateDataPointRequest,
    request: Request,
    config: ConfigService = Depends(get_config_service),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(get_current_principal),
    _perm=Depends(require_permission("config:write")),
):
    value = {
        "type": req.type,
        "description": req.description,
        "address": req.address,
        **({"bits": req.bits} if req.type == "DIGITAL" and req.bits else {}),
    }
    try:
        before, after, diff = config.add_leaf_by_absolute_path(req.path, req.name, value)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add data point: {e}")

    rev = ConfigRevision(
        action=f"legacy_add_data_point:{req.name}",
        yaml_path=str(config.path),
        before_yaml=before,
        after_yaml=after,
        diff=diff,
        user_id=principal.user.id if principal.user else None,
        client_ip=request.client.host if request.client else None,
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)
    audit.log(
        db,
        action="config.legacy_add_data_point",
        user_id=principal.user.id if principal.user else None,
        client_ip=request.client.host if request.client else None,
        resource=req.name,
        metadata={"actor": principal.actor_key, "parent_path": req.path, "revision_id": rev.id},
        config_revision_id=rev.id,
    )
    return {"message": f"Data point '{req.name}' added successfully to {req.path}.", "revision_id": rev.id}
