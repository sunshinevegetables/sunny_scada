from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_user, require_permission, get_audit_service
from sunny_scada.db.models import (
    Breakdown,
    Equipment,
    Instrument,
    InventoryTransaction,
    MaintenanceContainer,
    SparePart,
    Schedule,
    TaskTemplate,
    Vendor,
    WorkOrder,
)
from sunny_scada.services.maintenance_equipment_service import MaintenanceEquipmentService
from sunny_scada.services.maintenance_container_service import MaintenanceContainerService

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
_equipment_service = MaintenanceEquipmentService()
_container_service = MaintenanceContainerService()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _instrument_summary(db: Session, instrument_id: Optional[int]) -> Optional[dict[str, Any]]:
    if instrument_id is None:
        return None
    inst = db.query(Instrument).filter(Instrument.id == int(instrument_id)).one_or_none()
    if inst is None:
        return None
    return {
        "id": int(inst.id),
        "label": str(inst.label),
        "status": str(inst.status),
        "instrument_type": inst.instrument_type,
        "model": inst.model,
    }


def _apply_inventory_delta(
    db: Session,
    *,
    part: SparePart,
    qty_delta: int,
    reason: Optional[str],
    work_order_id: Optional[int],
    user_id: Optional[int],
    client_ip: Optional[str],
) -> InventoryTransaction:
    part.quantity_on_hand = int(part.quantity_on_hand or 0) + int(qty_delta)
    txn = InventoryTransaction(
        part_id=int(part.id),
        qty_delta=int(qty_delta),
        reason=reason,
        work_order_id=work_order_id,
        user_id=user_id,
        client_ip=client_ip,
    )
    db.add(part)
    db.add(txn)
    return txn


# ---------- Vendors ----------


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/vendors")
def create_vendor(
    req: VendorIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    if db.query(Vendor).filter(Vendor.name == req.name).first():
        raise HTTPException(status_code=400, detail="vendor exists")
    v = Vendor(name=req.name, phone=req.phone, email=req.email, notes=req.notes, meta=req.meta)
    db.add(v)
    db.commit()
    try:
        audit.log(db, action="maintenance.vendor.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=req.name, metadata={"id": v.id})
    except Exception:
        pass
    return {"id": v.id, "name": v.name}


@router.get("/vendors")
def list_vendors(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    vs = db.query(Vendor).order_by(Vendor.id.asc()).all()
    return [{"id": v.id, "name": v.name, "phone": v.phone, "email": v.email, "notes": v.notes, "meta": v.meta} for v in vs]


@router.get("/vendors/{vendor_id}")
def get_vendor(
    vendor_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": v.id, "name": v.name, "phone": v.phone, "email": v.email, "notes": v.notes, "meta": v.meta}


@router.put("/vendors/{vendor_id}")
def update_vendor(
    vendor_id: int,
    req: VendorIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Not found")
    v.name = req.name
    v.phone = req.phone
    v.email = req.email
    v.notes = req.notes
    v.meta = req.meta
    db.add(v)
    db.commit()
    try:
        audit.log(db, action="maintenance.vendor.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=v.name, metadata={"id": v.id})
    except Exception:
        pass
    return {"status": "ok"}


@router.delete("/vendors/{vendor_id}")
def delete_vendor(
    vendor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Not found")
    name = v.name
    db.delete(v)
    db.commit()
    try:
        audit.log(db, action="maintenance.vendor.delete", user_id=user.id, client_ip=request.client.host if request.client else None, resource=name, metadata={"id": vendor_id})
    except Exception:
        pass
    return {"status": "ok"}


# ---------- Equipment ----------


class EquipmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    vendor_id: Optional[int] = None
    container_id: Optional[int] = None
    parent_id: Optional[int] = None
    asset_category: Optional[str] = Field(default=None, max_length=50)
    asset_type: Optional[str] = Field(default=None, max_length=100)
    criticality: str = Field(default="B", max_length=10)
    duty_cycle_hours_per_day: Optional[float] = None
    spares_class: str = Field(default="standard", max_length=30)
    safety_classification: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class MaintenanceContainerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    parent_id: Optional[int] = None
    asset_category: Optional[str] = Field(default=None, max_length=50)
    asset_type: Optional[str] = Field(default=None, max_length=100)
    criticality: str = Field(default="B", max_length=10)
    duty_cycle_hours_per_day: Optional[float] = None
    spares_class: str = Field(default="standard", max_length=30)
    safety_classification: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/containers")
def create_maintenance_container(
    req: MaintenanceContainerIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    payload = req.model_dump()
    try:
        payload = _container_service.validate_payload(db, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    row = MaintenanceContainer(
        container_code="",
        name=req.name,
        location=req.location,
        description=req.description,
        parent_id=payload.get("parent_id"),
        asset_category=payload.get("asset_category"),
        asset_type=payload.get("asset_type"),
        criticality=payload.get("criticality") or "B",
        duty_cycle_hours_per_day=payload.get("duty_cycle_hours_per_day"),
        spares_class=payload.get("spares_class") or "standard",
        safety_classification=payload.get("safety_classification") or [],
        is_active=True,
        meta=req.meta,
    )
    db.add(row)
    db.flush()
    row.container_code = f"MC-{row.id:06d}"
    db.add(row)
    db.commit()
    try:
        audit.log(
            db,
            action="maintenance.container.create",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=row.container_code,
            metadata={"id": row.id},
        )
    except Exception:
        pass
    return _container_service.container_out(row)


@router.get("/containers")
def list_maintenance_containers(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    rows = db.query(MaintenanceContainer).order_by(MaintenanceContainer.id.asc()).all()
    return [_container_service.container_out(row) for row in rows]


@router.get("/containers/tree")
def maintenance_container_tree(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    return _container_service.build_tree(db)


@router.get("/containers/{container_id}")
def get_maintenance_container(
    container_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    row = db.query(MaintenanceContainer).filter(MaintenanceContainer.id == int(container_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _container_service.container_out(row)


@router.put("/containers/{container_id}")
def update_maintenance_container(
    container_id: int,
    req: MaintenanceContainerIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    row = db.query(MaintenanceContainer).filter(MaintenanceContainer.id == int(container_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    payload = req.model_dump()
    try:
        payload = _container_service.validate_payload(db, payload=payload, equipment_id=int(container_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    row.name = req.name
    row.location = req.location
    row.description = req.description
    row.parent_id = payload.get("parent_id")
    row.asset_category = payload.get("asset_category")
    row.asset_type = payload.get("asset_type")
    row.criticality = payload.get("criticality") or "B"
    row.duty_cycle_hours_per_day = payload.get("duty_cycle_hours_per_day")
    row.spares_class = payload.get("spares_class") or "standard"
    row.safety_classification = payload.get("safety_classification") or []
    row.meta = req.meta
    db.add(row)
    db.commit()
    try:
        audit.log(
            db,
            action="maintenance.container.update",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=row.container_code,
            metadata={"id": row.id},
        )
    except Exception:
        pass
    return {"status": "ok", "container": _container_service.container_out(row)}


@router.delete("/containers/{container_id}")
def delete_maintenance_container(
    container_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    row = db.query(MaintenanceContainer).filter(MaintenanceContainer.id == int(container_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    if db.query(Equipment.id).filter(Equipment.container_id == int(container_id)).first() is not None:
        raise HTTPException(status_code=400, detail="container has linked equipment")
    code = row.container_code
    db.delete(row)
    db.commit()
    try:
        audit.log(
            db,
            action="maintenance.container.delete",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=code,
            metadata={"id": int(container_id)},
        )
    except Exception:
        pass
    return {"status": "ok"}


@router.post("/equipment")
def create_equipment(
    req: EquipmentIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    payload = req.model_dump()
    try:
        payload = _equipment_service.validate_payload(db, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    eq = Equipment(
        equipment_code="",
        name=req.name,
        location=req.location,
        description=req.description,
        vendor_id=req.vendor_id,
        container_id=payload.get("container_id"),
        parent_id=payload.get("parent_id"),
        asset_category=payload.get("asset_category"),
        asset_type=payload.get("asset_type"),
        criticality=payload.get("criticality") or "B",
        duty_cycle_hours_per_day=payload.get("duty_cycle_hours_per_day"),
        spares_class=payload.get("spares_class") or "standard",
        safety_classification=payload.get("safety_classification") or [],
        is_active=True,
        meta=req.meta,
    )
    db.add(eq)
    db.flush()
    eq.equipment_code = f"EQ-{eq.id:06d}"
    db.add(eq)
    db.commit()
    try:
        audit.log(db, action="maintenance.equipment.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=eq.equipment_code, metadata={"id": eq.id})
    except Exception:
        pass
    return _equipment_service.equipment_out(eq)


@router.get("/equipment")
def list_equipment(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    rows = db.query(Equipment).order_by(Equipment.id.asc()).all()
    return [_equipment_service.equipment_out(e) for e in rows]


@router.get("/equipment/tree")
def equipment_tree(
    root_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    return _equipment_service.build_tree(db, root_id=root_id)


@router.get("/equipment/{equipment_id}")
def get_equipment(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    e = db.query(Equipment).filter(Equipment.id == equipment_id).one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    return _equipment_service.equipment_out(e)


@router.put("/equipment/{equipment_id}")
def update_equipment(
    equipment_id: int,
    req: EquipmentIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    e = db.query(Equipment).filter(Equipment.id == equipment_id).one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    payload = req.model_dump()
    try:
        payload = _equipment_service.validate_payload(db, payload=payload, equipment_id=int(equipment_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    e.name = req.name
    e.location = req.location
    e.description = req.description
    e.vendor_id = req.vendor_id
    e.container_id = payload.get("container_id")
    e.parent_id = payload.get("parent_id")
    e.asset_category = payload.get("asset_category")
    e.asset_type = payload.get("asset_type")
    e.criticality = payload.get("criticality") or "B"
    e.duty_cycle_hours_per_day = payload.get("duty_cycle_hours_per_day")
    e.spares_class = payload.get("spares_class") or "standard"
    e.safety_classification = payload.get("safety_classification") or []
    e.meta = req.meta
    db.add(e)
    db.commit()
    try:
        audit.log(db, action="maintenance.equipment.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=e.equipment_code, metadata={"id": e.id})
    except Exception:
        pass
    return {"status": "ok", "equipment": _equipment_service.equipment_out(e)}


@router.get("/equipment/{equipment_id}/path")
def equipment_path(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    try:
        path = _equipment_service.equipment_path(db, equipment_id=int(equipment_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    return {"path": path}


@router.get("/equipment/{equipment_id}/descendants")
def equipment_descendants(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    try:
        descendants = _equipment_service.descendants(db, equipment_id=int(equipment_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    return {"equipment_id": int(equipment_id), "descendants": descendants}


@router.delete("/equipment/{equipment_id}")
def delete_equipment(
    equipment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    e = db.query(Equipment).filter(Equipment.id == equipment_id).one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    code = e.equipment_code
    db.delete(e)
    db.commit()
    try:
        audit.log(db, action="maintenance.equipment.delete", user_id=user.id, client_ip=request.client.host if request.client else None, resource=code, metadata={"id": equipment_id})
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/equipment/{equipment_id}/history")
def equipment_history(
    equipment_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    # Timeline: breakdowns, work orders, inventory transactions
    breakdowns = db.query(Breakdown).filter(Breakdown.equipment_id == equipment_id).order_by(Breakdown.ts.desc()).all()
    work_orders = db.query(WorkOrder).filter(WorkOrder.equipment_id == equipment_id).order_by(WorkOrder.created_at.desc()).all()

    return {
        "breakdowns": [
            {
                "id": b.id,
                "ts": b.ts,
                "description": b.description,
                "severity": b.severity,
                "resolved": b.resolved,
                "resolved_at": b.resolved_at,
            }
            for b in breakdowns
        ],
        "work_orders": [
            {
                "id": w.id,
                "work_order_code": w.work_order_code,
                "status": w.status,
                "priority": w.priority,
                "title": w.title,
                "created_at": w.created_at,
                "due_at": w.due_at,
                "closed_at": w.closed_at,
            }
            for w in work_orders
        ],
    }


# ---------- Spare parts ----------


class SparePartIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    vendor_id: Optional[int] = None
    unit: Optional[str] = Field(default=None, max_length=50)
    quantity_on_hand: int = 0
    min_stock: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/spare_parts")
def create_spare_part(
    req: SparePartIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    sp = SparePart(part_code="", name=req.name, vendor_id=req.vendor_id, unit=req.unit, quantity_on_hand=int(req.quantity_on_hand), min_stock=int(req.min_stock), meta=req.meta)
    db.add(sp)
    db.flush()
    sp.part_code = f"SP-{sp.id:06d}"
    db.add(sp)
    db.commit()
    try:
        audit.log(db, action="maintenance.spare_part.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=sp.part_code, metadata={"id": sp.id})
    except Exception:
        pass
    return {"id": sp.id, "part_code": sp.part_code}


@router.get("/spare_parts")
def list_spare_parts(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    parts = db.query(SparePart).order_by(SparePart.id.asc()).all()
    return [
        {
            "id": p.id,
            "part_code": p.part_code,
            "name": p.name,
            "vendor_id": p.vendor_id,
            "unit": p.unit,
            "quantity_on_hand": p.quantity_on_hand,
            "min_stock": p.min_stock,
            "meta": p.meta,
        }
        for p in parts
    ]


@router.get("/spare_parts/min_stock")
def min_stock_alerts(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    parts = db.query(SparePart).filter(SparePart.quantity_on_hand <= SparePart.min_stock).all()
    return [{"id": p.id, "part_code": p.part_code, "name": p.name, "quantity_on_hand": p.quantity_on_hand, "min_stock": p.min_stock} for p in parts]


@router.get("/spare_parts/{part_id}")
def get_spare_part(
    part_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    p = db.query(SparePart).filter(SparePart.id == part_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": p.id, "part_code": p.part_code, "name": p.name, "vendor_id": p.vendor_id, "unit": p.unit, "quantity_on_hand": p.quantity_on_hand, "min_stock": p.min_stock, "meta": p.meta}


@router.put("/spare_parts/{part_id}")
def update_spare_part(
    part_id: int,
    req: SparePartIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    p = db.query(SparePart).filter(SparePart.id == part_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    p.name = req.name
    p.vendor_id = req.vendor_id
    p.unit = req.unit
    p.quantity_on_hand = int(req.quantity_on_hand)
    p.min_stock = int(req.min_stock)
    p.meta = req.meta
    db.add(p)
    db.commit()
    try:
        audit.log(db, action="maintenance.spare_part.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=p.part_code, metadata={"id": p.id})
    except Exception:
        pass
    return {"status": "ok"}


@router.delete("/spare_parts/{part_id}")
def delete_spare_part(
    part_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    p = db.query(SparePart).filter(SparePart.id == part_id).one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    code = p.part_code
    db.delete(p)
    db.commit()
    try:
        audit.log(db, action="maintenance.spare_part.delete", user_id=user.id, client_ip=request.client.host if request.client else None, resource=code, metadata={"id": part_id})
    except Exception:
        pass
    return {"status": "ok"}


class AdjustRequest(BaseModel):
    qty_delta: int
    reason: Optional[str] = Field(default=None, max_length=200)
    work_order_id: Optional[int] = None


@router.post("/spare_parts/{part_id}/adjust")
def adjust_inventory(
    part_id: int,
    req: AdjustRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("inventory:write")),
):
    part = db.query(SparePart).filter(SparePart.id == part_id).one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Not found")
    _apply_inventory_delta(
        db,
        part=part,
        qty_delta=int(req.qty_delta),
        reason=req.reason,
        work_order_id=req.work_order_id,
        user_id=user.id,
        client_ip=request.client.host if request.client else None,
    )
    db.commit()

    try:
        audit.log(db, action="maintenance.inventory.adjust", user_id=user.id, client_ip=request.client.host if request.client else None, resource=part.part_code, metadata={"qty_delta": req.qty_delta, "reason": req.reason})
    except Exception:
        pass

    return {"status": "ok", "quantity_on_hand": part.quantity_on_hand}


# ---------- Breakdowns ----------


class BreakdownIn(BaseModel):
    equipment_id: int
    description: str
    severity: str = Field(default="medium", max_length=30)
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/breakdowns")
def create_breakdown(
    req: BreakdownIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    b = Breakdown(equipment_id=req.equipment_id, description=req.description, severity=req.severity, resolved=False, reported_by_user_id=user.id, client_ip=request.client.host if request.client else None, meta=req.meta)
    db.add(b)
    db.commit()
    try:
        audit.log(db, action="maintenance.breakdown.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=str(req.equipment_id), metadata={"id": b.id})
    except Exception:
        pass
    return {"id": b.id}


@router.get("/breakdowns")
def list_breakdowns(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
    resolved: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=200),
):
    q = db.query(Breakdown)
    if resolved is not None:
        q = q.filter(Breakdown.resolved == bool(resolved))
    rows = q.order_by(Breakdown.ts.desc()).limit(limit).all()
    return [{"id": b.id, "ts": b.ts, "equipment_id": b.equipment_id, "description": b.description, "severity": b.severity, "resolved": b.resolved} for b in rows]


class BreakdownUpdate(BaseModel):
    resolved: Optional[bool] = None


@router.put("/breakdowns/{breakdown_id}")
def update_breakdown(
    breakdown_id: int,
    req: BreakdownUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    b = db.query(Breakdown).filter(Breakdown.id == breakdown_id).one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Not found")
    if req.resolved is not None:
        b.resolved = bool(req.resolved)
        b.resolved_at = _now() if b.resolved else None
    db.add(b)
    db.commit()
    try:
        audit.log(db, action="maintenance.breakdown.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=str(b.equipment_id), metadata={"id": b.id, "resolved": b.resolved})
    except Exception:
        pass
    return {"status": "ok"}


# ---------- Task templates ----------


class TaskTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    checklist: dict[str, Any] = Field(default_factory=dict)
    estimated_minutes: Optional[int] = None


@router.post("/task_templates")
def create_task_template(
    req: TaskTemplateIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    if db.query(TaskTemplate).filter(TaskTemplate.name == req.name).first():
        raise HTTPException(status_code=400, detail="exists")
    tt = TaskTemplate(name=req.name, description=req.description, checklist=req.checklist, estimated_minutes=req.estimated_minutes)
    db.add(tt)
    db.commit()
    try:
        audit.log(db, action="maintenance.task_template.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=req.name, metadata={"id": tt.id})
    except Exception:
        pass
    return {"id": tt.id}


@router.get("/task_templates")
def list_task_templates(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    rows = db.query(TaskTemplate).order_by(TaskTemplate.id.asc()).all()
    return [{"id": t.id, "name": t.name, "description": t.description, "checklist": t.checklist, "estimated_minutes": t.estimated_minutes} for t in rows]


@router.put("/task_templates/{template_id}")
def update_task_template(
    template_id: int,
    req: TaskTemplateIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    tt = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).one_or_none()
    if not tt:
        raise HTTPException(status_code=404, detail="Not found")
    tt.name = req.name
    tt.description = req.description
    tt.checklist = req.checklist
    tt.estimated_minutes = req.estimated_minutes
    db.add(tt)
    db.commit()
    try:
        audit.log(db, action="maintenance.task_template.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=tt.name, metadata={"id": tt.id})
    except Exception:
        pass
    return {"status": "ok"}


@router.delete("/task_templates/{template_id}")
def delete_task_template(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    tt = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).one_or_none()
    if not tt:
        raise HTTPException(status_code=404, detail="Not found")
    name = tt.name
    db.delete(tt)
    db.commit()
    try:
        audit.log(db, action="maintenance.task_template.delete", user_id=user.id, client_ip=request.client.host if request.client else None, resource=name, metadata={"id": template_id})
    except Exception:
        pass
    return {"status": "ok"}


# ---------- Schedules ----------


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    cron: Optional[str] = Field(default=None, max_length=200)
    interval_minutes: Optional[int] = None
    task_template_id: Optional[int] = None
    equipment_id: Optional[int] = None
    instrument_id: Optional[int] = None
    meta: dict[str, Any] = Field(default_factory=dict)


def _validate_schedule_target(equipment_id: Optional[int], instrument_id: Optional[int]) -> None:
    if (equipment_id is None) == (instrument_id is None):
        raise HTTPException(status_code=400, detail="Provide exactly one of equipment_id or instrument_id")


@router.post("/schedules")
def create_schedule(
    req: ScheduleIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    _validate_schedule_target(req.equipment_id, req.instrument_id)
    sch = Schedule(
        name=req.name,
        enabled=req.enabled,
        cron=req.cron,
        interval_minutes=req.interval_minutes,
        task_template_id=req.task_template_id,
        equipment_id=req.equipment_id,
        instrument_id=req.instrument_id,
        next_run_at=_now(),
        meta=req.meta,
    )
    db.add(sch)
    db.commit()
    try:
        audit.log(db, action="maintenance.schedule.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=req.name, metadata={"id": sch.id})
    except Exception:
        pass
    return {"id": sch.id}


@router.get("/schedules")
def list_schedules(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    rows = db.query(Schedule).order_by(Schedule.id.asc()).all()
    return [{"id": s.id, "name": s.name, "enabled": s.enabled, "cron": s.cron, "interval_minutes": s.interval_minutes, "next_run_at": s.next_run_at, "equipment_id": s.equipment_id, "instrument_id": s.instrument_id, "task_template_id": s.task_template_id, "meta": s.meta} for s in rows]


@router.put("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: int,
    req: ScheduleIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    _validate_schedule_target(req.equipment_id, req.instrument_id)
    sch = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
    if not sch:
        raise HTTPException(status_code=404, detail="Not found")
    sch.name = req.name
    sch.enabled = req.enabled
    sch.cron = req.cron
    sch.interval_minutes = req.interval_minutes
    sch.task_template_id = req.task_template_id
    sch.equipment_id = req.equipment_id
    sch.instrument_id = req.instrument_id
    sch.meta = req.meta
    if sch.next_run_at is None:
        sch.next_run_at = _now()
    db.add(sch)
    db.commit()
    try:
        audit.log(db, action="maintenance.schedule.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=sch.name, metadata={"id": sch.id})
    except Exception:
        pass
    return {"status": "ok"}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    sch = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
    if not sch:
        raise HTTPException(status_code=404, detail="Not found")
    name = sch.name
    db.delete(sch)
    db.commit()
    try:
        audit.log(db, action="maintenance.schedule.delete", user_id=user.id, client_ip=request.client.host if request.client else None, resource=name, metadata={"id": schedule_id})
    except Exception:
        pass
    return {"status": "ok"}


# ---------- Work orders ----------


class WorkOrderIn(BaseModel):
    equipment_id: Optional[int] = None
    instrument_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    priority: str = Field(default="normal", max_length=30)
    assigned_user_id: Optional[int] = None
    assigned_role_id: Optional[int] = None
    due_at: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/work_orders")
def create_work_order(
    req: WorkOrderIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    due = dt.datetime.fromisoformat(req.due_at.replace("Z", "+00:00")) if req.due_at else None
    wo = WorkOrder(
        work_order_code="",
        equipment_id=req.equipment_id,
        instrument_id=req.instrument_id,
        title=req.title,
        description=req.description,
        status="open",
        priority=req.priority,
        assigned_user_id=req.assigned_user_id,
        assigned_role_id=req.assigned_role_id,
        due_at=due,
        meta=req.meta,
    )
    db.add(wo)
    db.flush()
    wo.work_order_code = f"WO-{wo.id:06d}"
    db.add(wo)
    db.commit()

    try:
        audit.log(db, action="maintenance.work_order.create", user_id=user.id, client_ip=request.client.host if request.client else None, resource=wo.work_order_code, metadata={"id": wo.id})
    except Exception:
        pass

    return {"id": wo.id, "work_order_code": wo.work_order_code}


@router.get("/work_orders")
def list_work_orders(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
    instrument_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200),
):
    q = db.query(WorkOrder)
    if instrument_id is not None:
        q = q.filter(WorkOrder.instrument_id == int(instrument_id))
    if status:
        q = q.filter(WorkOrder.status == status)
    rows = q.order_by(WorkOrder.created_at.desc()).limit(limit).all()
    return [
        {
            "id": w.id,
            "work_order_code": w.work_order_code,
            "equipment_id": w.equipment_id,
            "instrument_id": w.instrument_id,
            "instrument": _instrument_summary(db, w.instrument_id),
            "status": w.status,
            "priority": w.priority,
            "title": w.title,
            "created_at": w.created_at,
            "due_at": w.due_at,
            "assigned_user_id": w.assigned_user_id,
            "assigned_role_id": w.assigned_role_id,
        }
        for w in rows
    ]


@router.get("/work_orders/{work_order_id}")
def get_work_order(
    work_order_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    w = db.query(WorkOrder).filter(WorkOrder.id == work_order_id).one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": w.id,
        "work_order_code": w.work_order_code,
        "equipment_id": w.equipment_id,
        "instrument_id": w.instrument_id,
        "instrument": _instrument_summary(db, w.instrument_id),
        "status": w.status,
        "priority": w.priority,
        "title": w.title,
        "description": w.description,
        "created_at": w.created_at,
        "due_at": w.due_at,
        "assigned_user_id": w.assigned_user_id,
        "assigned_role_id": w.assigned_role_id,
        "meta": w.meta,
    }


class StatusChange(BaseModel):
    status: str = Field(min_length=1, max_length=30)


class WorkOrderPartUsed(BaseModel):
    part_id: int
    qty_used: int = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=200)


class WorkOrderStatusChange(StatusChange):
    parts_used: Optional[list[WorkOrderPartUsed]] = None


_ALLOWED = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


@router.post("/work_orders/{work_order_id}/status")
def set_work_order_status(
    work_order_id: int,
    req: WorkOrderStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    w = db.query(WorkOrder).filter(WorkOrder.id == work_order_id).one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Not found")
    new = req.status
    cur = w.status
    if new != cur and new not in _ALLOWED.get(cur, set()):
        raise HTTPException(status_code=400, detail="Invalid status transition")

    if new == "done" and req.parts_used:
        for item in req.parts_used:
            part = db.query(SparePart).filter(SparePart.id == int(item.part_id)).one_or_none()
            if not part:
                raise HTTPException(status_code=404, detail=f"Part not found: {item.part_id}")
            _apply_inventory_delta(
                db,
                part=part,
                qty_delta=-int(item.qty_used),
                reason=item.reason or "issue",
                work_order_id=w.id,
                user_id=user.id,
                client_ip=request.client.host if request.client else None,
            )

    w.status = new
    if new in ("done", "cancelled"):
        w.closed_at = _now()
    db.add(w)
    db.commit()

    try:
        audit.log(db, action="maintenance.work_order.status", user_id=user.id, client_ip=request.client.host if request.client else None, resource=w.work_order_code, metadata={"from": cur, "to": new})
    except Exception:
        pass

    return {"status": "ok", "work_order_code": w.work_order_code, "new": new}


class WorkOrderUpdate(BaseModel):
    instrument_id: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    priority: Optional[str] = Field(default=None, max_length=30)
    assigned_user_id: Optional[int] = None
    assigned_role_id: Optional[int] = None
    due_at: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


@router.put("/work_orders/{work_order_id}")
def update_work_order(
    work_order_id: int,
    req: WorkOrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    w = db.query(WorkOrder).filter(WorkOrder.id == work_order_id).one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Not found")
    if req.title is not None:
        w.title = req.title
    if req.instrument_id is not None:
        w.instrument_id = req.instrument_id
    if req.description is not None:
        w.description = req.description
    if req.priority is not None:
        w.priority = req.priority
    if req.assigned_user_id is not None:
        w.assigned_user_id = req.assigned_user_id
    if req.assigned_role_id is not None:
        w.assigned_role_id = req.assigned_role_id
    if req.due_at is not None:
        w.due_at = dt.datetime.fromisoformat(req.due_at.replace("Z", "+00:00")) if req.due_at else None
    if req.meta is not None:
        w.meta = req.meta
    db.add(w)
    db.commit()

    try:
        audit.log(db, action="maintenance.work_order.update", user_id=user.id, client_ip=request.client.host if request.client else None, resource=w.work_order_code, metadata={"id": w.id})
    except Exception:
        pass

    return {"status": "ok"}


@router.delete("/work_orders/{work_order_id}")
def delete_work_order(
    work_order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    w = db.query(WorkOrder).filter(WorkOrder.id == work_order_id).one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Not found")
    code = w.work_order_code
    db.delete(w)
    db.commit()

    try:
        audit.log(db, action="maintenance.work_order.delete", user_id=user.id, client_ip=request.client.host if request.client else None, resource=code, metadata={"id": work_order_id})
    except Exception:
        pass

    return {"status": "ok"}


class PartUsageRequest(BaseModel):
    part_id: int
    qty_used: int = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=200)


@router.post("/work_orders/{work_order_id}/parts")
def use_part(
    work_order_id: int,
    req: PartUsageRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("inventory:write")),
):
    w = db.query(WorkOrder).filter(WorkOrder.id == work_order_id).one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Work order not found")
    part = db.query(SparePart).filter(SparePart.id == req.part_id).one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")

    _apply_inventory_delta(
        db,
        part=part,
        qty_delta=-int(req.qty_used),
        reason=req.reason or "issue",
        work_order_id=w.id,
        user_id=user.id,
        client_ip=request.client.host if request.client else None,
    )
    db.commit()

    try:
        audit.log(db, action="maintenance.work_order.part_use", user_id=user.id, client_ip=request.client.host if request.client else None, resource=w.work_order_code, metadata={"part": part.part_code, "qty": req.qty_used})
    except Exception:
        pass

    return {"status": "ok", "quantity_on_hand": part.quantity_on_hand}
