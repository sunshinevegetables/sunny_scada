from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_db,
    get_current_user,
    get_audit_service,
    get_settings,
    require_permission,
    get_auth_service,
    get_access_control_service,
    require_resource_read,
    require_resource_write,
)
from sunny_scada.core.settings import Settings
from sunny_scada.db.models import (
    CfgPLC,
    CfgContainer,
    CfgEquipment,
    CfgContainerType,
    CfgEquipmentType,
    CfgDataPoint,
    CfgDataPointClass,
    CfgDataPointUnit,
    CfgDataPointGroup,
)
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.system_config_service import SystemConfigService, validate_ip_or_hostname


router = APIRouter(prefix="/api/config", tags=["system-config"])


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _is_acl_admin(db: Session, auth, user) -> bool:
    """Users with global admin permissions can bypass object-level filtering."""

    perms = auth.user_permissions(db, user)
    return ("users:admin" in perms) or ("roles:admin" in perms)


# ----------------
# Schemas
# ----------------


class PLCIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ip: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    groupId: Optional[int] = None

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        return validate_ip_or_hostname(v)


class PLCPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    ip: Optional[str] = Field(default=None, min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    groupId: Optional[int] = None

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
    groupId: Optional[int] = None


class ContainerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)
    groupId: Optional[int] = None


class ContainerPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    type: Optional[str] = Field(default=None, min_length=1, max_length=200)
    groupId: Optional[int] = None


class ContainerOut(BaseModel):
    id: int
    plc_id: int
    name: str
    type: str
    groupId: Optional[int] = None


class EquipmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)
    groupId: Optional[int] = None


class EquipmentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    type: Optional[str] = Field(default=None, min_length=1, max_length=200)
    groupId: Optional[int] = None


class EquipmentOut(BaseModel):
    id: int
    container_id: int
    name: str
    type: str
    groupId: Optional[int] = None


class DataPointIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    category: str = Field(pattern=r"^(read|write)$")
    type: str = Field(pattern=r"^(INTEGER|DIGITAL|REAL)$")
    address: str = Field(min_length=1, max_length=200)
    groupId: Optional[int] = None
    classId: Optional[int] = None
    unitId: Optional[int] = None
    multiplier: float = Field(default=1.0, gt=0)
    bitLabels: Optional[Dict[int, str]] = None
    bitPositions: Optional[Dict[int, Dict[str, Optional[str]]]] = None


class DataPointPatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = Field(default=None, pattern=r"^(read|write)$")
    type: Optional[str] = Field(default=None, pattern=r"^(INTEGER|DIGITAL|REAL)$")
    address: Optional[str] = Field(default=None, min_length=1, max_length=200)
    groupId: Optional[int] = None
    classId: Optional[int] = None
    unitId: Optional[int] = None
    multiplier: Optional[float] = Field(default=None, gt=0)
    bitLabels: Optional[Dict[int, str]] = None
    bitPositions: Optional[Dict[int, Dict[str, Optional[str]]]] = None


class DataPointOut(BaseModel):
    id: int
    owner_type: str
    owner_id: int
    label: str
    description: Optional[str]
    category: str
    type: str
    address: str
    groupId: Optional[int] = None
    classId: Optional[int] = None
    unitId: Optional[int] = None
    multiplier: int
    bitLabels: Dict[int, str] = Field(default_factory=dict)
    bitPositions: Dict[int, Dict[str, Optional[str]]] = Field(default_factory=dict)


class MetaOptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)


class MetaOptionPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)


class MetaOptionOut(BaseModel):
    id: int
    name: str
    description: Optional[str]


def _dp_out(dp: CfgDataPoint) -> Dict[str, Any]:
    bit_labels: Dict[int, str] = {}
    bit_positions: Dict[int, Dict[str, Optional[str]]] = {}
    if dp.type == "DIGITAL":
        for b in dp.bits or []:
            bit_labels[int(b.bit)] = b.label
            bit_positions[int(b.bit)] = {
                "label": b.label,
                "class": getattr(b, "bit_class", None),
            }
    return {
        "id": dp.id,
        "owner_type": dp.owner_type,
        "owner_id": dp.owner_id,
        "label": dp.label,
        "description": dp.description,
        "category": dp.category,
        "type": dp.type,
        "address": dp.address,
        "groupId": dp.group_id,
        "classId": dp.class_id,
        "unitId": dp.unit_id,
        "multiplier": int(dp.multiplier or 1),
        "bitLabels": bit_labels,
        "bitPositions": bit_positions,
    }


def _svc(settings: Settings) -> SystemConfigService:
    return SystemConfigService(digital_bit_max=settings.digital_bit_max)


@router.get("/tree")
def get_config_tree(
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
):
    """Return the full PLC→Container→Equipment hierarchy (with datapoints).

    Non-admins are filtered by effective ACL read access.
    """

    is_admin = _is_acl_admin(db, auth, me)
    ea = None if is_admin else ac.effective_access(db, me)

    plcs = db.query(CfgPLC).order_by(CfgPLC.id.asc()).all()
    containers = db.query(CfgContainer).order_by(CfgContainer.id.asc()).all()
    equipment = db.query(CfgEquipment).order_by(CfgEquipment.id.asc()).all()
    datapoints = db.query(CfgDataPoint).order_by(CfgDataPoint.id.asc()).all()

    plc_nodes: Dict[int, Dict[str, Any]] = {}
    for p in plcs:
        if ea is not None and p.id not in ea.read_plc_ids:
            continue
        plc_nodes[p.id] = {
            "type": "plc",
            "id": p.id,
            "name": p.name,
            "ip": p.ip,
            "port": p.port,
            "groupId": p.group_id,
            "containers": [],
            "datapoints": [],
        }

    container_nodes: Dict[int, Dict[str, Any]] = {}
    for c in containers:
        if ea is not None and c.id not in ea.read_container_ids:
            continue
        container_nodes[c.id] = {
            "type": "container",
            "id": c.id,
            "plc_id": c.plc_id,
                "name": c.name,
                "containerType": c.type,
                "groupId": c.group_id,
            "equipment": [],
            "datapoints": [],
        }

    equipment_nodes: Dict[int, Dict[str, Any]] = {}
    for e in equipment:
        if ea is not None and e.id not in ea.read_equipment_ids:
            continue
        equipment_nodes[e.id] = {
            "type": "equipment",
            "id": e.id,
            "container_id": e.container_id,
                "name": e.name,
                "equipmentType": e.type,
                "groupId": e.group_id,
            "datapoints": [],
        }

    # Attach containers → PLC
    for c in container_nodes.values():
        parent = plc_nodes.get(int(c["plc_id"]))
        if parent is not None:
            parent["containers"].append(c)

    # Attach equipment → Container
    for e in equipment_nodes.values():
        parent = container_nodes.get(int(e["container_id"]))
        if parent is not None:
            parent["equipment"].append(e)

    # Attach datapoints
    for dp in datapoints:
        if ea is not None and dp.id not in ea.read_datapoint_ids:
            continue
        dto = _dp_out(dp)
        if dp.owner_type == "plc":
            node = plc_nodes.get(int(dp.owner_id))
            if node is not None:
                node["datapoints"].append(dto)
        elif dp.owner_type == "container":
            node = container_nodes.get(int(dp.owner_id))
            if node is not None:
                node["datapoints"].append(dto)
        elif dp.owner_type == "equipment":
            node = equipment_nodes.get(int(dp.owner_id))
            if node is not None:
                node["datapoints"].append(dto)

    # Sort children (stable UI)
    for p in plc_nodes.values():
        p["containers"].sort(key=lambda x: x["id"])
        p["datapoints"].sort(key=lambda x: x["id"])
        for c in p["containers"]:
            c["equipment"].sort(key=lambda x: x["id"])
            c["datapoints"].sort(key=lambda x: x["id"])
            for e in c["equipment"]:
                e["datapoints"].sort(key=lambda x: x["id"])

    return sorted(plc_nodes.values(), key=lambda x: x["id"])


# ----------------
# Datapoint Meta (Classes / Units / Groups)
# ----------------


@router.get("/datapoint-classes", response_model=list[MetaOptionOut])
def list_datapoint_classes(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    items = svc.list_datapoint_classes(db)
    return [MetaOptionOut(id=i.id, name=i.name, description=i.description) for i in items]


@router.post("/datapoint-classes", response_model=MetaOptionOut)
def create_datapoint_class(
    req: MetaOptionIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    try:
        obj = svc.create_datapoint_class(db, name=req.name, description=req.description, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_class.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "name": obj.name},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.patch("/datapoint-classes/{class_id}", response_model=MetaOptionOut)
def patch_datapoint_class(
    class_id: int,
    req: MetaOptionPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_datapoint_class(db, class_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Class not found")
    try:
        obj = svc.update_datapoint_class(db, obj, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_class.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.delete("/datapoint-classes/{class_id}")
def delete_datapoint_class(
    class_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_datapoint_class(db, class_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Class not found")
    name = obj.name
    try:
        svc.delete_datapoint_class(db, obj)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_class.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"id": class_id, "name": name},
        )
    except Exception:
        pass

    return {"ok": True}


@router.get("/datapoint-units", response_model=list[MetaOptionOut])
def list_datapoint_units(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    items = svc.list_datapoint_units(db)
    return [MetaOptionOut(id=i.id, name=i.name, description=i.description) for i in items]


@router.post("/datapoint-units", response_model=MetaOptionOut)
def create_datapoint_unit(
    req: MetaOptionIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    try:
        obj = svc.create_datapoint_unit(db, name=req.name, description=req.description, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_unit.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "name": obj.name},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.patch("/datapoint-units/{unit_id}", response_model=MetaOptionOut)
def patch_datapoint_unit(
    unit_id: int,
    req: MetaOptionPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_datapoint_unit(db, unit_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Unit not found")
    try:
        obj = svc.update_datapoint_unit(db, obj, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_unit.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.delete("/datapoint-units/{unit_id}")
def delete_datapoint_unit(
    unit_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_datapoint_unit(db, unit_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Unit not found")
    name = obj.name
    try:
        svc.delete_datapoint_unit(db, obj)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_unit.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"id": unit_id, "name": name},
        )
    except Exception:
        pass

    return {"ok": True}


@router.get("/datapoint-groups", response_model=list[MetaOptionOut])
def list_datapoint_groups(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    items = svc.list_datapoint_groups(db)
    return [MetaOptionOut(id=i.id, name=i.name, description=i.description) for i in items]


@router.get("/container-types", response_model=list[MetaOptionOut])
def list_container_types(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    items = svc.list_container_types(db)
    return [MetaOptionOut(id=i.id, name=i.name, description=i.description) for i in items]


@router.post("/container-types", response_model=MetaOptionOut)
def create_container_type(
    req: MetaOptionIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    try:
        obj = svc.create_container_type(db, name=req.name, description=req.description, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.container_type.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "name": obj.name},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.patch("/container-types/{type_id}", response_model=MetaOptionOut)
def patch_container_type(
    type_id: int,
    req: MetaOptionPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_container_type(db, type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Container type not found")
    try:
        obj = svc.update_container_type(db, obj, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.container_type.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.delete("/container-types/{type_id}")
def delete_container_type(
    type_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_container_type(db, type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Container type not found")
    name = obj.name
    try:
        svc.delete_container_type(db, obj)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.container_type.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"id": type_id, "name": name},
        )
    except Exception:
        pass

    return {"ok": True}


@router.get("/equipment-types", response_model=list[MetaOptionOut])
def list_equipment_types(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    items = svc.list_equipment_types(db)
    return [MetaOptionOut(id=i.id, name=i.name, description=i.description) for i in items]


@router.post("/equipment-types", response_model=MetaOptionOut)
def create_equipment_type(
    req: MetaOptionIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    try:
        obj = svc.create_equipment_type(db, name=req.name, description=req.description, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.equipment_type.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "name": obj.name},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.patch("/equipment-types/{type_id}", response_model=MetaOptionOut)
def patch_equipment_type(
    type_id: int,
    req: MetaOptionPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_equipment_type(db, type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Equipment type not found")
    try:
        obj = svc.update_equipment_type(db, obj, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.equipment_type.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.delete("/equipment-types/{type_id}")
def delete_equipment_type(
    type_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_equipment_type(db, type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Equipment type not found")
    name = obj.name
    try:
        svc.delete_equipment_type(db, obj)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.equipment_type.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"id": type_id, "name": name},
        )
    except Exception:
        pass

    return {"ok": True}


@router.post("/datapoint-groups", response_model=MetaOptionOut)
def create_datapoint_group(
    req: MetaOptionIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    try:
        obj = svc.create_datapoint_group(db, name=req.name, description=req.description, user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_group.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "name": obj.name},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.patch("/datapoint-groups/{group_id}", response_model=MetaOptionOut)
def patch_datapoint_group(
    group_id: int,
    req: MetaOptionPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_datapoint_group(db, group_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        obj = svc.update_datapoint_group(db, obj, patch=req.model_dump(exclude_unset=True), user_id=me.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_group.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=obj.name,
            metadata={"id": obj.id, "patch": req.model_dump(exclude_unset=True)},
        )
    except Exception:
        pass

    return MetaOptionOut(id=obj.id, name=obj.name, description=obj.description)


@router.delete("/datapoint-groups/{group_id}")
def delete_datapoint_group(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    obj = svc.get_datapoint_group(db, group_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Group not found")
    name = obj.name
    try:
        svc.delete_datapoint_group(db, obj)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audit.log(
            db,
            action="system_config.datapoint_group.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=name,
            metadata={"id": group_id, "name": name},
        )
    except Exception:
        pass

    return {"ok": True}


# ----------------
# PLC endpoints
# ----------------


@router.get("/plcs", response_model=list[PLCOut])
def list_plcs(
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plcs = svc.list_plcs(db)
    if not _is_acl_admin(db, auth, me):
        ea = ac.effective_access(db, me)
        plcs = [p for p in plcs if p.id in ea.read_plc_ids]
    return [PLCOut(id=p.id, name=p.name, ip=p.ip, port=p.port, groupId=p.group_id) for p in plcs]


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
        plc = svc.create_plc(db, name=req.name, ip=req.ip, port=req.port, group_id=req.groupId, user_id=me.id)
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
    return PLCOut(id=plc.id, name=plc.name, ip=plc.ip, port=plc.port, groupId=plc.group_id)


@router.get("/plcs/{plc_id}", response_model=PLCOut)
def get_plc(
    plc_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("plc")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plc = svc.get_plc(db, plc_id)
    if not plc:
        raise HTTPException(status_code=404, detail="PLC not found")
    return PLCOut(id=plc.id, name=plc.name, ip=plc.ip, port=plc.port, groupId=plc.group_id)


@router.patch("/plcs/{plc_id}", response_model=PLCOut)
def patch_plc(
    plc_id: int,
    req: PLCPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("plc")),
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

    return PLCOut(id=plc.id, name=plc.name, ip=plc.ip, port=plc.port, groupId=plc.group_id)


@router.delete("/plcs/{plc_id}")
def delete_plc(
    plc_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("plc")),
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
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("plc")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    if not svc.get_plc(db, plc_id):
        raise HTTPException(status_code=404, detail="PLC not found")
    items = svc.list_containers(db, plc_id)
    if not _is_acl_admin(db, auth, me):
        ea = ac.effective_access(db, me)
        items = [c for c in items if c.id in ea.read_container_ids]
    return [ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type, groupId=c.group_id) for c in items]


@router.post("/plcs/{plc_id}/containers", response_model=ContainerOut)
def create_container(
    plc_id: int,
    req: ContainerIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("plc")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    plc = svc.get_plc(db, plc_id)
    if not plc:
        raise HTTPException(status_code=404, detail="PLC not found")
    try:
        c = svc.create_container(db, plc=plc, name=req.name, type_=req.type, group_id=req.groupId, user_id=me.id)
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
    return ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type, groupId=c.group_id)


@router.get("/containers/{container_id}", response_model=ContainerOut)
def get_container(
    container_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("container")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    c = svc.get_container(db, container_id)
    if not c:
        raise HTTPException(status_code=404, detail="Container not found")
    return ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type, groupId=c.group_id)


@router.patch("/containers/{container_id}", response_model=ContainerOut)
def patch_container(
    container_id: int,
    req: ContainerPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("container")),
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
    return ContainerOut(id=c.id, plc_id=c.plc_id, name=c.name, type=c.type, groupId=c.group_id)


@router.delete("/containers/{container_id}")
def delete_container(
    container_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("container")),
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
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("container")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    if not svc.get_container(db, container_id):
        raise HTTPException(status_code=404, detail="Container not found")
    items = svc.list_equipment(db, container_id)
    if not _is_acl_admin(db, auth, me):
        ea = ac.effective_access(db, me)
        items = [e for e in items if e.id in ea.read_equipment_ids]
    return [EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type, groupId=e.group_id) for e in items]


@router.post("/containers/{container_id}/equipment", response_model=EquipmentOut)
def create_equipment(
    container_id: int,
    req: EquipmentIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("container")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    container = svc.get_container(db, container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    try:
        e = svc.create_equipment(db, container=container, name=req.name, type_=req.type, group_id=req.groupId, user_id=me.id)
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
    return EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type, groupId=e.group_id)


@router.get("/equipment/{equipment_id}", response_model=EquipmentOut)
def get_equipment(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("equipment")),
    settings: Settings = Depends(get_settings),
):
    svc = _svc(settings)
    e = svc.get_equipment(db, equipment_id)
    if not e:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type, groupId=e.group_id)


@router.patch("/equipment/{equipment_id}", response_model=EquipmentOut)
def patch_equipment(
    equipment_id: int,
    req: EquipmentPatch,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("equipment")),
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
    return EquipmentOut(id=e.id, container_id=e.container_id, name=e.name, type=e.type, groupId=e.group_id)


@router.delete("/equipment/{equipment_id}")
def delete_equipment(
    equipment_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("equipment")),
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
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("plc")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "plc", plc_id)
    svc = _svc(settings)
    dps = svc.list_data_points(db, owner_type="plc", owner_id=plc_id)
    if not _is_acl_admin(db, auth, me):
        ea = ac.effective_access(db, me)
        dps = [dp for dp in dps if dp.id in ea.read_datapoint_ids]
    return [DataPointOut(**_dp_out(dp)) for dp in dps]


@router.post("/plcs/{plc_id}/data-points", response_model=DataPointOut)
def create_plc_data_point(
    plc_id: int,
    req: DataPointIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("plc")),
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
            group_id=req.groupId,
            class_id=req.classId,
            unit_id=req.unitId,
            multiplier=req.multiplier,
            bit_labels=req.bitLabels,
            bit_positions=req.bitPositions,
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
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("container")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "container", container_id)
    svc = _svc(settings)
    dps = svc.list_data_points(db, owner_type="container", owner_id=container_id)
    if not _is_acl_admin(db, auth, me):
        ea = ac.effective_access(db, me)
        dps = [dp for dp in dps if dp.id in ea.read_datapoint_ids]
    return [DataPointOut(**_dp_out(dp)) for dp in dps]


@router.post("/containers/{container_id}/data-points", response_model=DataPointOut)
def create_container_data_point(
    container_id: int,
    req: DataPointIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("container")),
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
            group_id=req.groupId,
            class_id=req.classId,
            unit_id=req.unitId,
            multiplier=req.multiplier,
            bit_labels=req.bitLabels,
            bit_positions=req.bitPositions,
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
    me=Depends(get_current_user),
    auth=Depends(get_auth_service),
    ac=Depends(get_access_control_service),
    _perm=Depends(require_permission("config:read")),
    _acl=Depends(require_resource_read("equipment")),
    settings: Settings = Depends(get_settings),
):
    _ensure_owner_exists(db, "equipment", equipment_id)
    svc = _svc(settings)
    dps = svc.list_data_points(db, owner_type="equipment", owner_id=equipment_id)
    if not _is_acl_admin(db, auth, me):
        ea = ac.effective_access(db, me)
        dps = [dp for dp in dps if dp.id in ea.read_datapoint_ids]
    return [DataPointOut(**_dp_out(dp)) for dp in dps]


@router.post("/equipment/{equipment_id}/data-points", response_model=DataPointOut)
def create_equipment_data_point(
    equipment_id: int,
    req: DataPointIn,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    me=Depends(get_current_user),
    _perm=Depends(require_permission("config:write")),
    _acl=Depends(require_resource_write("equipment")),
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
            group_id=req.groupId,
            class_id=req.classId,
            unit_id=req.unitId,
            multiplier=req.multiplier,
            bit_labels=req.bitLabels,
            bit_positions=req.bitPositions,
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
    _acl=Depends(require_resource_read("datapoint")),
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
    _acl=Depends(require_resource_write("datapoint")),
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
    _acl=Depends(require_resource_write("datapoint")),
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
