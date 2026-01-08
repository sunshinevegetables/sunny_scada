from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_user, get_audit_service, get_settings, require_permission
from sunny_scada.core.settings import Settings
from sunny_scada.db.models import CfgPLC, CfgContainer, CfgEquipment, CfgDataPoint
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.system_config_service import SystemConfigService, validate_ip_or_hostname


router = APIRouter(prefix="/api/config", tags=["system-config"])


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


# ----------------
# Schemas
# ----------------


class PLCIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ip: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        return validate_ip_or_hostname(v)


class PLCPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    ip: Optional[str] = Field(default=None, min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return validate_ip_or_hostname(v)


class PLCOut(BaseModel):
    id: int
    name: str
    ip: str
    port: int


class ContainerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)


class ContainerPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    type: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ContainerOut(BaseModel):
    id: int
    plc_id: int
    name: str
    type: str


class EquipmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)


class EquipmentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    type: Optional[str] = Field(default=None, min_length=1, max_length=200)


class EquipmentOut(BaseModel):
    id: int
    container_id: int
    name: str
    type: str


class DataPointIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    category: str = Field(pattern=r"^(read|write)$")
    type: str = Field(pattern=r"^(INTEGER|DIGITAL|REAL)$")
    address: str = Field(min_length=1, max_length=200)
    bitLabels: Optional[Dict[int, str]] = None


class DataPointPatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = Field(default=None, pattern=r"^(read|write)$")
    type: Optional[str] = Field(default=None, pattern=r"^(INTEGER|DIGITAL|REAL)$")
    address: Optional[str] = Field(default=None, min_length=1, max_length=200)
    bitLabels: Optional[Dict[int, str]] = None


class DataPointOut(BaseModel):
    id: int
    owner_type: str
    owner_id: int
    label: str
    description: Optional[str]
    category: str
    type: str
    address: str
    bitLabels: Dict[int, str] = Field(default_factory=dict)


def _dp_out(dp: CfgDataPoint) -> Dict[str, Any]:
    bit_labels: Dict[int, str] = {}
    if dp.type == "DIGITAL":
        for b in dp.bits or []:
            bit_labels[int(b.bit)] = b.label
    return {
        "id": dp.id,
        "owner_type": dp.owner_type,
        "owner_id": dp.owner_id,
        "label": dp.label,
        "description": dp.description,
        "category": dp.category,
        "type": dp.type,
        "address": dp.address,
        "bitLabels": bit_labels,
    }


def _svc(settings: Settings) -> SystemConfigService:
    return SystemConfigService(digital_bit_max=settings.digital_bit_max)


# ----------------
# PLC endpoints
# ----------------


@router.get("/plcs", response_model=list[PLCOut])
def list_plcs(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    return [PLCOut(id=p.id, name=p.name, ip=p.ip, port=p.port) for p in svc.list_plcs(db)]


@router.post("/plcs", response_model=PLCOut)
def create_plc(
    req: PLCIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    try:
        plc = svc.create_plc(db, name=req.name, ip=req.ip, port=req.port, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.plc.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=plc.name,
            metadata={"plc_id": plc.id},
        )
    except Exception:
        pass
    return PLCOut(id=plc.id, name=plc.name, ip=plc.ip, port=plc.port)


@router.get("/plcs/{plc_id}", response_model=PLCOut)
def get_plc(
    plc_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plc = svc.get_plc(db, plc_id)
    if not plc:
        raise HTTPException(status_code=404, detail="PLC not found")
    return PLCOut(id=plc.id, name=plc.name, ip=plc.ip, port=plc.port)


@router.patch("/plcs/{plc_id}", response_model=PLCOut)
def patch_plc(
    plc_id: int,
    req: PLCPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plc = svc.get_plc(db, plc_id)
    if not plc:
        raise HTTPException(status_code=404, detail="PLC not found")
    try:
        plc = svc.update_plc(db, plc, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.plc.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=plc.name,
            metadata={"plc_id": plc.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass

    return PLCOut(id=plc.id, name=plc.name, ip=plc.ip, port=plc.port)


@router.delete("/plcs/{plc_id}")
def delete_plc(
    plc_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plc = svc.get_plc(db, plc_id)
    if not plc:
        raise HTTPException(status_code=404, detail="PLC not found")
    name = plc.name
    try:
        svc.delete_plc(db, plc, force=bool(force))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.plc.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"plc_id": plc_id, "force": bool(force)},
        )
    except Exception:
        pass

    return {"ok": True}


# ----------------
# Container endpoints
# ----------------


@router.get("/plcs/{plc_id}/containers", response_model=list[ContainerOut])
def list_containers(
    plc_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    if not svc.get_plc(db, plc_id):
        raise HTTPException(status_code=404, detail="PLC not found")
    items = svc.list_containers(db, plc_id)
    return [ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type) for c in items]


@router.post("/plcs/{plc_id}/containers", response_model=ContainerOut)
def create_container(
    plc_id: int,
    req: ContainerIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plc = svc.get_plc(db, plc_id)
    if not plc:
        raise HTTPException(status_code=404, detail="PLC not found")
    try:
        c = svc.create_container(db, plc=plc, name=req.name, type_=req.type, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.container.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=c.name,
            metadata={"container_id": c.id, "plc_id": plc_id},
        )
    except Exception:
        pass
    return ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type)


@router.get("/containers/{container_id}", response_model=ContainerOut)
def get_container(
    container_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    c = svc.get_container(db, container_id)
    if not c:
        raise HTTPException(status_code=404, detail="Container not found")
    return ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type)


@router.patch("/containers/{container_id}", response_model=ContainerOut)
def patch_container(
    container_id: int,
    req: ContainerPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    c = svc.get_container(db, container_id)
    if not c:
        raise HTTPException(status_code=404, detail="Container not found")
    try:
        c = svc.update_container(db, c, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.container.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=c.name,
            metadata={"container_id": c.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass
    return ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type)


@router.delete("/containers/{container_id}")
def delete_container(
    container_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    c = svc.get_container(db, container_id)
    if not c:
        raise HTTPException(status_code=404, detail="Container not found")
    name = c.name
    try:
        svc.delete_container(db, c, force=bool(force))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.container.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"container_id": container_id, "force": bool(force)},
        )
    except Exception:
        pass
    return {"ok": True}


# ----------------
# Equipment endpoints
# ----------------


@router.get("/containers/{container_id}/equipment", response_model=list[EquipmentOut])
def list_equipment(
    container_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    if not svc.get_container(db, container_id):
        raise HTTPException(status_code=404, detail="Container not found")
    items = svc.list_equipment(db, container_id)
    return [EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type) for e in items]


@router.post("/containers/{container_id}/equipment", response_model=EquipmentOut)
def create_equipment(
    container_id: int,
    req: EquipmentIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    container = svc.get_container(db, container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    try:
        e = svc.create_equipment(db, container=container, name=req.name, type_=req.type, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.equipment.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=e.name,
            metadata={"equipment_id": e.id, "container_id": container_id},
        )
    except Exception:
        pass
    return EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type)


@router.get("/equipment/{equipment_id}", response_model=EquipmentOut)
def get_equipment(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    e = svc.get_equipment(db, equipment_id)
    if not e:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type)


@router.patch("/equipment/{equipment_id}", response_model=EquipmentOut)
def patch_equipment(
    equipment_id: int,
    req: EquipmentPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    e = svc.get_equipment(db, equipment_id)
    if not e:
        raise HTTPException(status_code=404, detail="Equipment not found")
    try:
        e = svc.update_equipment(db, e, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.equipment.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=e.name,
            metadata={"equipment_id": e.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass
    return EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type)


@router.delete("/equipment/{equipment_id}")
def delete_equipment(
    equipment_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    e = svc.get_equipment(db, equipment_id)
    if not e:
        raise HTTPException(status_code=404, detail="Equipment not found")
    name = e.name
    try:
        svc.delete_equipment(db, e, force=bool(force))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.equipment.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"equipment_id": equipment_id, "force": bool(force)},
        )
    except Exception:
        pass
    return {"ok": True}


# ----------------
# Data points endpoints
# ----------------


def _ensure_owner_exists(db: Session, owner_type: str, owner_id: int) -> None:
    if owner_type == "plc":
        if not db.query(CfgPLC).filter(CfgPLC.id == owner_id).one_or_none():
            raise HTTPException(status_code=404, detail="PLC not found")
    elif owner_type == "container":
        if not db.query(CfgContainer).filter(CfgContainer.id == owner_id).one_or_none():
            raise HTTPException(status_code=404, detail="Container not found")
    elif owner_type == "equipment":
        if not db.query(CfgEquipment).filter(CfgEquipment.id == owner_id).one_or_none():
            raise HTTPException(status_code=404, detail="Equipment not found")
    else:
        raise HTTPException(status_code=400, detail="invalid owner")


@router.get("/plcs/{plc_id}/data-points", response_model=list[DataPointOut])
def list_plc_data_points(
    plc_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "plc", plc_id)
    svc = _svc(settings)
    return [DataPointOut(**_dp_out(dp)) for dp in svc.list_data_points(db, owner_type="plc", owner_id=plc_id)]


@router.post("/plcs/{plc_id}/data-points", response_model=DataPointOut)
def create_plc_data_point(
    plc_id: int,
    req: DataPointIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "plc", plc_id)
    svc = _svc(settings)
    try:
        dp = svc.create_data_point(
            db,
            owner_type="plc",
            owner_id=plc_id,
            label=req.label,
            description=req.description,
            category=req.category,
            type_=req.type,
            address=req.address,
            bit_labels=req.bitLabels,
            user_id=me.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.datapoint.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=dp.label,
            metadata={"data_point_id": dp.id, "owner_type": "plc", "owner_id": plc_id},
        )
    except Exception:
        pass
    return DataPointOut(**_dp_out(dp))


@router.get("/containers/{container_id}/data-points", response_model=list[DataPointOut])
def list_container_data_points(
    container_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "container", container_id)
    svc = _svc(settings)
    return [DataPointOut(**_dp_out(dp)) for dp in svc.list_data_points(db, owner_type="container", owner_id=container_id)]


@router.post("/containers/{container_id}/data-points", response_model=DataPointOut)
def create_container_data_point(
    container_id: int,
    req: DataPointIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "container", container_id)
    svc = _svc(settings)
    try:
        dp = svc.create_data_point(
            db,
            owner_type="container",
            owner_id=container_id,
            label=req.label,
            description=req.description,
            category=req.category,
            type_=req.type,
            address=req.address,
            bit_labels=req.bitLabels,
            user_id=me.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.datapoint.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=dp.label,
            metadata={"data_point_id": dp.id, "owner_type": "container", "owner_id": container_id},
        )
    except Exception:
        pass
    return DataPointOut(**_dp_out(dp))


@router.get("/equipment/{equipment_id}/data-points", response_model=list[DataPointOut])
def list_equipment_data_points(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "equipment", equipment_id)
    svc = _svc(settings)
    return [DataPointOut(**_dp_out(dp)) for dp in svc.list_data_points(db, owner_type="equipment", owner_id=equipment_id)]


@router.post("/equipment/{equipment_id}/data-points", response_model=DataPointOut)
def create_equipment_data_point(
    equipment_id: int,
    req: DataPointIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "equipment", equipment_id)
    svc = _svc(settings)
    try:
        dp = svc.create_data_point(
            db,
            owner_type="equipment",
            owner_id=equipment_id,
            label=req.label,
            description=req.description,
            category=req.category,
            type_=req.type,
            address=req.address,
            bit_labels=req.bitLabels,
            user_id=me.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.datapoint.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=dp.label,
            metadata={"data_point_id": dp.id, "owner_type": "equipment", "owner_id": equipment_id},
        )
    except Exception:
        pass
    return DataPointOut(**_dp_out(dp))


@router.get("/data-points/{data_point_id}", response_model=DataPointOut)
def get_data_point(
    data_point_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    dp = svc.get_data_point(db, data_point_id)
    if not dp:
        raise HTTPException(status_code=404, detail="Data point not found")
    return DataPointOut(**_dp_out(dp))


@router.patch("/data-points/{data_point_id}", response_model=DataPointOut)
def patch_data_point(
    data_point_id: int,
    req: DataPointPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    dp = svc.get_data_point(db, data_point_id)
    if not dp:
        raise HTTPException(status_code=404, detail="Data point not found")
    try:
        dp = svc.update_data_point(db, dp, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        audit.log(
            db,
            action="system_config.datapoint.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=dp.label,
            metadata={"data_point_id": dp.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass
    return DataPointOut(**_dp_out(dp))


@router.delete("/data-points/{data_point_id}")
def delete_data_point(
    data_point_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    dp = svc.get_data_point(db, data_point_id)
    if not dp:
        raise HTTPException(status_code=404, detail="Data point not found")
    label = dp.label
    svc.delete_data_point(db, dp)
    try:
        audit.log(
            db,
            action="system_config.datapoint.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=label,
            metadata={"data_point_id": data_point_id},
        )
    except Exception:
        pass
    return {"ok": True}
