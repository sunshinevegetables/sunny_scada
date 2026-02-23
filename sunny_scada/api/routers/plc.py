from __future__ import annotations

import logging
import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, selectinload

from sunny_scada.api.schemas import BitReadSignalRequest, BitWriteSignalRequest
from sunny_scada.api.deps import (
    get_storage,
    get_reader,
    get_data_points_service,
    get_db,
    get_current_principal,
    require_permission,
    get_audit_service,
    get_command_service,
    get_access_control_service,
    get_system_config_service,
)
from sunny_scada.api.security import Principal
from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgEquipment, CfgPLC
from sunny_scada.plc_reader import address_4x_to_pymodbus
from sunny_scada.services.access_control_service import AccessControlService
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.data_points_service import DataPointsService

router = APIRouter(tags=["plc"])
logger = logging.getLogger(__name__)


_INT_GROUP_RE = re.compile(r"\d+")


def _extract_int_address(address: str | None) -> int | None:
    if not address:
        return None
    a = str(address).strip()
    if not a:
        return None
    if a.isdigit():
        return int(a)
    groups = _INT_GROUP_RE.findall(a)
    if not groups:
        return None
    return int(max(groups, key=len))


def _candidate_register_addresses(address: str | None) -> list[int]:
    n = _extract_int_address(address)
    if n is None:
        return []
    out: list[int] = [n]
    if n >= 40001:
        try:
            out.insert(0, address_4x_to_pymodbus(n))
        except Exception:
            pass
    seen: set[int] = set()
    uniq: list[int] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


def _is_leaf(node: Any) -> bool:
    return isinstance(node, dict) and (
        "register_address" in node or any(k in node for k in ("value", "raw_value", "scaled_value"))
    )


def _collect_leaf_indexes(storage_tree: Any) -> tuple[Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    by_label: Dict[str, Dict[str, Any]] = {}
    by_reg: Dict[int, Dict[str, Any]] = {}

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            if _is_leaf(v):
                by_label.setdefault(str(k), v)
                payload_label = v.get("label")
                if payload_label:
                    by_label.setdefault(str(payload_label), v)
                try:
                    ra = v.get("register_address")
                    if ra is not None:
                        by_reg.setdefault(int(ra), v)
                except Exception:
                    pass
            if isinstance(v, dict):
                _walk(v)

    _walk(storage_tree)
    return by_label, by_reg


def _resolve_value(dp: CfgDataPoint, leaf: Dict[str, Any] | None) -> Any | None:
    if not isinstance(leaf, dict):
        return None

    typ = str(dp.type or "").upper()
    if typ == "REAL":
        value = leaf.get("scaled_value")
        if value is None:
            value = leaf.get("raw_value")
        if value is None:
            value = leaf.get("value")
    elif typ == "INTEGER":
        value = leaf.get("value")
    elif typ == "DIGITAL":
        raw_bits = leaf.get("value")
        if isinstance(raw_bits, dict):
            if dp.bits:
                out: Dict[str, bool] = {}
                for b in sorted(dp.bits, key=lambda x: (int(getattr(x, "bit", 0)), int(getattr(x, "id", 0)))):
                    bit_key = f"BIT {int(b.bit)}"
                    raw = raw_bits.get(bit_key)
                    if isinstance(raw, dict):
                        out[str(b.label)] = bool(raw.get("value"))
                    else:
                        out[str(b.label)] = bool(raw)
                value = out
            else:
                value = raw_bits
        else:
            value = None
    else:
        value = leaf.get("value")

    try:
        mult = float(dp.multiplier or 1.0)
    except Exception:
        mult = 1.0
    if isinstance(value, (int, float)) and mult != 1.0:
        return float(value) * mult
    return value


@router.get(
    "/plc_data",
    summary="Get PLC Data",
    description="Fetch the latest data from all configured PLCs.",
)
def get_plc_data(
    storage=Depends(get_storage),
    reader=Depends(get_reader),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    ac: AccessControlService = Depends(get_access_control_service),
    _perm=Depends(require_permission("plc:read")),
):
    # Admin bypass convention: users:admin OR roles:admin skips object-level ACL filtering.
    perms = principal.permissions or set()
    admin_bypass = ("users:admin" in perms) or ("roles:admin" in perms)

    if admin_bypass:
        ea = None
    else:
        if principal.type == "user" and principal.user:
            ea = ac.effective_access(db, principal.user)
        else:
            ea = ac.effective_access_for_role_ids(db, role_ids=principal.role_ids)

    def _can_read_plc(plc_id: int) -> bool:
        if admin_bypass:
            return True
        return bool(ea) and int(plc_id) in ea.read_plc_ids

    def _can_read_container(container_id: int) -> bool:
        if admin_bypass:
            return True
        return bool(ea) and int(container_id) in ea.read_container_ids

    def _can_read_equipment(equipment_id: int) -> bool:
        if admin_bypass:
            return True
        return bool(ea) and int(equipment_id) in ea.read_equipment_ids

    def _can_read_datapoint(dp_id: int) -> bool:
        if admin_bypass:
            return True
        return bool(ea) and int(dp_id) in ea.read_datapoint_ids

    # Batch-load config graph (avoid N+1)
    plcs = db.query(CfgPLC).order_by(CfgPLC.id.asc()).all()
    containers = db.query(CfgContainer).order_by(CfgContainer.id.asc()).all()
    equipment = db.query(CfgEquipment).order_by(CfgEquipment.id.asc()).all()
    datapoints = (
        db.query(CfgDataPoint)
        .options(
            selectinload(CfgDataPoint.bits),
            selectinload(CfgDataPoint.dp_group),
            selectinload(CfgDataPoint.dp_class),
            selectinload(CfgDataPoint.dp_unit),
        )
        .order_by(CfgDataPoint.id.asc())
        .all()
    )

    containers_by_plc: Dict[int, list[CfgContainer]] = {}
    for c in containers:
        containers_by_plc.setdefault(int(c.plc_id), []).append(c)

    equipment_by_container: Dict[int, list[CfgEquipment]] = {}
    for e in equipment:
        equipment_by_container.setdefault(int(e.container_id), []).append(e)

    datapoints_by_owner: Dict[tuple[str, int], list[CfgDataPoint]] = {}
    for dp in datapoints:
        key = (str(dp.owner_type or "").strip().lower(), int(dp.owner_id))
        datapoints_by_owner.setdefault(key, []).append(dp)

    # Snapshot data (YAML-shaped) is the live value source.
    storage_data = storage.get_data()
    _refreshed_once = False

    def _refresh_storage_once() -> None:
        """If storage is empty or missing PLC keys, do a best-effort immediate poll once."""
        nonlocal storage_data, _refreshed_once
        if _refreshed_once:
            return
        _refreshed_once = True
        try:
            # Safe: updates DataStorage even if reads fail (it will still stamp a timestamp).
            reader.read_plcs_from_config()
        except Exception:
            pass
        storage_data = storage.get_data()

    def _storage_snapshot_for_db_plc(plc_obj: CfgPLC) -> Dict[str, Any]:
        """Resolve the correct storage snapshot for a DB PLC.

        Why this exists
        --------------
        The poller stores snapshots keyed by the PLC *name* from config/config.yaml.
        The System Config module stores PLCs in the DB (name, ip, port). Those names
        often don't match (e.g., DB: "Sunshine" vs YAML: "Main PLC").

        Resolution strategy:
          1) Exact name match (DB name == poller name)
          2) Fuzzy name match (case/whitespace)
          3) Match by ip+port using PLCReader.config_data to find the poller-name
          4) If still missing, do one immediate poll and retry
        """

        def _norm(s: str) -> str:
            return "".join(str(s).strip().lower().split())

        # 1) Exact key match
        snap = storage_data.get(str(plc_obj.name))
        if isinstance(snap, dict):
            return snap

        # 2) Fuzzy name match
        tgt = _norm(str(plc_obj.name))
        for k, v in storage_data.items():
            if _norm(str(k)) == tgt and isinstance(v, dict):
                return v

        # 3) Match by ip+port against YAML config (PLCReader.config_data)
        try:
            plc_ip = str(plc_obj.ip).strip().lower()
            plc_port = int(plc_obj.port or 502)
        except Exception:
            plc_ip = str(plc_obj.ip).strip().lower()
            plc_port = 502

        candidate_names: list[str] = []
        try:
            for _, devices in (getattr(reader, "config_data", {}) or {}).items():
                if not isinstance(devices, list):
                    continue
                for dev in devices:
                    if not isinstance(dev, dict):
                        continue
                    dev_ip = str(dev.get("ip") or "").strip().lower()
                    if not dev_ip:
                        continue
                    dev_port = int(dev.get("port") or 502)
                    if dev_ip == plc_ip and dev_port == plc_port:
                        nm = str(dev.get("name") or "").strip()
                        if nm:
                            candidate_names.append(nm)
        except Exception:
            candidate_names = []

        for nm in candidate_names:
            snap = storage_data.get(nm)
            if isinstance(snap, dict):
                return snap

        # 4) One refresh + retry
        _refresh_storage_once()
        snap = storage_data.get(str(plc_obj.name))
        if isinstance(snap, dict):
            return snap
        for nm in candidate_names:
            snap = storage_data.get(nm)
            if isinstance(snap, dict):
                return snap

        return {}

    def _datapoints_out(
        owner_type: str,
        owner_id: int,
        *,
        leaf_by_label: Dict[str, Dict[str, Any]],
        leaf_by_reg: Dict[int, Dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for dp in datapoints_by_owner.get((owner_type, int(owner_id)), []):
            if str(dp.category or "").strip().lower() != "read":
                continue
            if not _can_read_datapoint(int(dp.id)):
                continue

            leaf = leaf_by_label.get(str(dp.label))
            if leaf is None:
                for ra in _candidate_register_addresses(getattr(dp, "address", None)):
                    leaf = leaf_by_reg.get(int(ra))
                    if leaf is not None:
                        break
            value = _resolve_value(dp, leaf)

            bits_meta = [
                {
                    "id": int(getattr(b, "id", 0)),
                    "data_point_id": int(getattr(b, "data_point_id", 0)),
                    "bit": int(b.bit),
                    "label": str(b.label),
                    "bit_class": b.bit_class,
                }
                for b in sorted(dp.bits or [], key=lambda b: (int(getattr(b, "bit", 0)), int(getattr(b, "id", 0))))
            ]

            out.append(
                {
                    "id": int(dp.id),
                    "owner_type": str(dp.owner_type),
                    "owner_id": int(dp.owner_id),
                    "label": str(dp.label),
                    "description": dp.description,
                    "value": value,
                    "address": dp.address,
                    "category": str(dp.category),
                    "type": str(dp.type),
                    "group": (
                        {
                            "id": int(dp.dp_group.id),
                            "name": str(dp.dp_group.name),
                            "description": dp.dp_group.description,
                        }
                        if getattr(dp, "dp_group", None) is not None
                        else None
                    ),
                    "class": (
                        {
                            "id": int(dp.dp_class.id),
                            "name": str(dp.dp_class.name),
                            "description": dp.dp_class.description,
                        }
                        if getattr(dp, "dp_class", None) is not None
                        else None
                    ),
                    "unit": (
                        {
                            "id": int(dp.dp_unit.id),
                            "name": str(dp.dp_unit.name),
                            "description": dp.dp_unit.description,
                        }
                        if getattr(dp, "dp_unit", None) is not None
                        else None
                    ),
                    "multiplier": float(dp.multiplier or 1.0),
                    "created_at": dp.created_at,
                    "updated_at": dp.updated_at,
                    "created_by_user_id": getattr(dp, "created_by_user_id", None),
                    "updated_by_user_id": getattr(dp, "updated_by_user_id", None),
                    "bits": bits_meta,
                }
            )
        return out

    out_plcs: list[dict[str, Any]] = []

    for plc in plcs:
        if not _can_read_plc(int(plc.id)):
            continue

        snap = _storage_snapshot_for_db_plc(plc)
        plc_timestamp = snap.get("timestamp") if isinstance(snap, dict) else None
        plc_tree = snap.get("data") if isinstance(snap, dict) else None
        if not isinstance(plc_tree, dict):
            plc_tree = {}
        leaf_by_label, leaf_by_reg = _collect_leaf_indexes(plc_tree)

        plc_datapoints = _datapoints_out(
            "plc",
            int(plc.id),
            leaf_by_label=leaf_by_label,
            leaf_by_reg=leaf_by_reg,
        )

        out_containers: list[dict[str, Any]] = []
        for c in containers_by_plc.get(int(plc.id), []):
            if not _can_read_container(int(c.id)):
                continue

            container_datapoints = _datapoints_out(
                "container",
                int(c.id),
                leaf_by_label=leaf_by_label,
                leaf_by_reg=leaf_by_reg,
            )

            out_equipment: list[dict[str, Any]] = []
            for e in equipment_by_container.get(int(c.id), []):
                if not _can_read_equipment(int(e.id)):
                    continue

                equipment_datapoints = _datapoints_out(
                    "equipment",
                    int(e.id),
                    leaf_by_label=leaf_by_label,
                    leaf_by_reg=leaf_by_reg,
                )

                # Prune empty equipment
                if not equipment_datapoints:
                    continue

                out_equipment.append(
                    {
                        "id": int(e.id),
                        "name": str(e.name),
                        "equipmentType": str(e.type),
                        "datapoints": equipment_datapoints,
                    }
                )

            # Prune empty containers
            if not container_datapoints and not out_equipment:
                continue

            out_containers.append(
                {
                    "id": int(c.id),
                    "name": str(c.name),
                    "containerType": str(c.type),
                    "datapoints": container_datapoints,
                    "equipment": out_equipment,
                }
            )

        # Prune empty PLCs
        if not plc_datapoints and not out_containers:
            continue

        out_plcs.append(
            {
                "id": int(plc.id),
                "name": str(plc.name),
                "timestamp": plc_timestamp,
                "datapoints": plc_datapoints,
                "containers": out_containers,
            }
        )

    # Log a concise view of the data returned by /plc_data for developer visibility.
    try:
        log = logging.getLogger(__name__)
        for plc in out_plcs:
            plc_name = str(plc.get("name") or "")
            # Top-level datapoints (not in containers)
            for dp in plc.get("datapoints", []) or []:
                label = dp.get("label")
                value = dp.get("value")
                log.info(f"{plc_name} -> {label} -> {value}")

            # Containers
            for c in plc.get("containers", []) or []:
                c_label = c.get("name")
                # Container-level datapoints
                for dp in c.get("datapoints", []) or []:
                    label = dp.get("label")
                    value = dp.get("value")
                    log.info(f"{c_label} -> {label} -> {value}")

                # Equipment within container
                for eq in c.get("equipment", []) or []:
                    eq_label = eq.get("name")
                    for dp in eq.get("datapoints", []) or []:
                        label = dp.get("label")
                        value = dp.get("value")
                        log.info(f"{c_label} -> {eq_label} -> {label} -> {value}")
    except Exception:
        # Ensure logging doesn't break endpoint
        pass

    return {"plcs": out_plcs}


@router.post(
    "/bit_read_signal",
    summary="Read a Bit Signal from PLC",
    description="Read a specific bit from a Modbus register.",
)
def bit_read_signal(
    req: BitReadSignalRequest,
    reader=Depends(get_reader),
    dp: DataPointsService = Depends(get_data_points_service),
    _perm=Depends(require_permission("command:read")),
):
    # Find register in YAML (read section)
    target = dp.find_register(req.register, direction="read")
    if not target:
        raise HTTPException(status_code=400, detail=f"Register '{req.register}' not recognized in read points.")

    register_address = target.get("address")
    bits = target.get("bits", {}) or {}
    if register_address is None or f"BIT {req.bit}" not in bits:
        raise HTTPException(status_code=400, detail=f"Invalid bit '{req.bit}' for register '{req.register}'.")

    bit_value = reader.read_single_bit(req.plc_name, int(register_address), int(req.bit))
    if bit_value is None:
        raise HTTPException(status_code=500, detail="Failed to read bit signal.")

    return {
        "message": f"Successfully read value {bit_value} from bit {req.bit} of register {req.register} on {req.plc_name}",
        "value": bit_value,
    }


@router.post(
    "/bit_write_signal",
    summary="Write a Bit Signal to PLC",
    description="Send a bitwise signal to a specific PLC using DB-configured datapoints.",
)
def bit_write_signal(
    req: BitWriteSignalRequest,
    request: Request,
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(get_current_principal),
    svc=Depends(get_command_service),
    sys_cfg=Depends(get_system_config_service),
    _perm=Depends(require_permission("command:write")),
):
    """DB-driven bit write endpoint."""
    equipment_id: int | None = None
    if req.equipmentId is not None and str(req.equipmentId).strip() != "":
        try:
            equipment_id = int(str(req.equipmentId).strip())
        except Exception:
            raise HTTPException(status_code=400, detail="equipmentId must be an integer")

    # Look up write datapoint in DB
    try:
        db_datapoint = sys_cfg.find_write_data_point_by_equipment(
            db,
            plc_name=req.plc,
            equipment_label=req.equipmentLabel,
            command_tag=req.commandTag,
            equipment_id=equipment_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not db_datapoint:
        raise HTTPException(
            status_code=400,
            detail=f"Write datapoint not found for PLC '{req.plc}', equipment '{req.equipmentLabel}', tag '{req.commandTag}'",
        )
    
    # Extract address and validate bit
    raw_address = db_datapoint.address
    try:
        register_address = int(raw_address)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid address format in DB: {raw_address}")
    
    # Check if bit is allowed (by checking the bit table)
    allowed_bits = {b.bit for b in db_datapoint.bits}
    if allowed_bits and req.bit not in allowed_bits:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid bit '{req.bit}' for command tag '{req.commandTag}'. Allowed bits: {sorted(allowed_bits)}",
        )
    
    # Use DB internal ID for tracing
    datapoint_id = f"db-dp:{db_datapoint.id}"
    
    # Queue a secure command (non-blocking).
    try:
        res = svc.create(
            db,
            plc_name=req.plc,
            datapoint_id=datapoint_id,
            kind="bit",
            value=req.value,
            bit=req.bit,
            user_id=principal.user.id if principal.user else None,
            client_ip=request.client.host if request.client else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("bit_write_signal failed")
        raise HTTPException(status_code=400, detail=f"Failed to queue bit write: {exc}")

    # Traceability (audit)
    try:
        audit.log(
            db,
            action="plc.bit_write_signal",
            user_id=principal.user.id if principal.user else None,
            client_ip=request.client.host if request.client else None,
            resource=req.plc,
            metadata={
                "actor": principal.actor_key,
                "datapoint_id": datapoint_id,
                "bit": req.bit,
                "value": req.value,
                "command_id": res.command_id,
            },
        )
    except Exception:
        pass

    return {
        "message": f"Queued write of value {req.value} to bit {req.bit} for datapoint {datapoint_id} on {req.plc}",
        "command_id": res.command_id,
        "status": res.status,
    }
