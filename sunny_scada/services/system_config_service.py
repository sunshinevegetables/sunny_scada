from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import CfgPLC, CfgContainer, CfgEquipment, CfgDataPoint, CfgDataPointBit


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,199}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,199}$")
_ADDR_RE = re.compile(r"^[A-Za-z0-9_:\.\/\[\]\(\)\-]+$")


def is_valid_hostname(value: str) -> bool:
    # Explicitly allow hostnames (RFC-ish), and also allow "localhost".
    v = value.strip()
    if not v or len(v) > 253:
        return False
    if v.lower() == "localhost":
        return True
    if v.endswith("."):
        v = v[:-1]
    labels = v.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?", label):
            return False
    return True


def validate_ip_or_hostname(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("ip is required")
    try:
        ipaddress.ip_address(v)
        return v
    except Exception:
        if is_valid_hostname(v):
            return v
        raise ValueError("ip must be a valid IPv4/IPv6 address or hostname")


def validate_name(value: str, *, field: str = "name") -> str:
    v = value.strip()
    if not v:
        raise ValueError(f"{field} is required")
    if not _NAME_RE.fullmatch(v):
        raise ValueError(f"{field} contains invalid characters")
    return v


def validate_label(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("label is required")
    if not _LABEL_RE.fullmatch(v):
        raise ValueError("label contains invalid characters")
    return v


def validate_address(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("address is required")
    if len(v) > 200:
        raise ValueError("address too long")
    if not _ADDR_RE.fullmatch(v):
        raise ValueError("address contains invalid characters")
    return v


def _dedupe_bits(bits: Dict[int, str]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for k, v in bits.items():
        if v is None:
            continue
        label = str(v).strip()
        if not label:
            continue
        out[int(k)] = label
    return out


@dataclass
class SystemConfigService:
    digital_bit_max: int = 15

    # ---------------- PLC ----------------
    def list_plcs(self, db: Session) -> list[CfgPLC]:
        return db.query(CfgPLC).order_by(CfgPLC.id.asc()).all()

    def get_plc(self, db: Session, plc_id: int) -> Optional[CfgPLC]:
        return db.query(CfgPLC).filter(CfgPLC.id == plc_id).one_or_none()

    def create_plc(self, db: Session, *, name: str, ip: str, port: int, user_id: Optional[int]) -> CfgPLC:
        name = validate_name(name)
        ip = validate_ip_or_hostname(ip)
        if port < 1 or port > 65535:
            raise ValueError("port must be between 1 and 65535")

        if db.query(CfgPLC).filter(CfgPLC.name == name).first():
            raise ValueError("PLC name already exists")

        plc = CfgPLC(name=name, ip=ip, port=port, created_by_user_id=user_id, updated_by_user_id=user_id)
        db.add(plc)
        db.commit()
        db.refresh(plc)
        return plc

    def update_plc(self, db: Session, plc: CfgPLC, *, patch: Dict[str, Any], user_id: Optional[int]) -> CfgPLC:
        if "name" in patch and patch["name"] is not None:
            name = validate_name(str(patch["name"]))
            if name != plc.name and db.query(CfgPLC).filter(CfgPLC.name == name).first():
                raise ValueError("PLC name already exists")
            plc.name = name
        if "ip" in patch and patch["ip"] is not None:
            plc.ip = validate_ip_or_hostname(str(patch["ip"]))
        if "port" in patch and patch["port"] is not None:
            port = int(patch["port"])
            if port < 1 or port > 65535:
                raise ValueError("port must be between 1 and 65535")
            plc.port = port

        plc.updated_by_user_id = user_id
        db.add(plc)
        db.commit()
        db.refresh(plc)
        return plc

    def delete_plc(self, db: Session, plc: CfgPLC, *, force: bool) -> None:
        container_count = db.query(CfgContainer).filter(CfgContainer.plc_id == plc.id).count()
        dp_count = db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "plc", CfgDataPoint.owner_id == plc.id).count()

        if (container_count or dp_count) and not force:
            raise ValueError("PLC has dependent resources; pass force=true to delete")

        if force:
            # delete datapoints owned by PLC, and by descendants (containers/equipment)
            container_ids = [row[0] for row in db.query(CfgContainer.id).filter(CfgContainer.plc_id == plc.id).all()]
            equipment_ids: list[int] = []
            if container_ids:
                equipment_ids = [row[0] for row in db.query(CfgEquipment.id).filter(CfgEquipment.container_id.in_(container_ids)).all()]

            db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "plc", CfgDataPoint.owner_id == plc.id).delete(synchronize_session=False)
            if container_ids:
                db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "container", CfgDataPoint.owner_id.in_(container_ids)).delete(synchronize_session=False)
            if equipment_ids:
                db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "equipment", CfgDataPoint.owner_id.in_(equipment_ids)).delete(synchronize_session=False)

        db.delete(plc)
        db.commit()

    # ---------------- Containers ----------------
    def list_containers(self, db: Session, plc_id: int) -> list[CfgContainer]:
        return db.query(CfgContainer).filter(CfgContainer.plc_id == plc_id).order_by(CfgContainer.id.asc()).all()

    def get_container(self, db: Session, container_id: int) -> Optional[CfgContainer]:
        return db.query(CfgContainer).filter(CfgContainer.id == container_id).one_or_none()

    def create_container(self, db: Session, *, plc: CfgPLC, name: str, type_: str, user_id: Optional[int]) -> CfgContainer:
        name = validate_name(name)
        type_ = validate_name(type_, field="type")
        if db.query(CfgContainer).filter(CfgContainer.plc_id == plc.id, CfgContainer.name == name).first():
            raise ValueError("Container name already exists in PLC")
        c = CfgContainer(
            plc_id=plc.id,
            name=name,
            type=type_,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    def update_container(self, db: Session, container: CfgContainer, *, patch: Dict[str, Any], user_id: Optional[int]) -> CfgContainer:
        if "name" in patch and patch["name"] is not None:
            name = validate_name(str(patch["name"]))
            if name != container.name and db.query(CfgContainer).filter(CfgContainer.plc_id == container.plc_id, CfgContainer.name == name).first():
                raise ValueError("Container name already exists in PLC")
            container.name = name
        if "type" in patch and patch["type"] is not None:
            container.type = validate_name(str(patch["type"]), field="type")
        container.updated_by_user_id = user_id
        db.add(container)
        db.commit()
        db.refresh(container)
        return container

    def delete_container(self, db: Session, container: CfgContainer, *, force: bool) -> None:
        eq_count = db.query(CfgEquipment).filter(CfgEquipment.container_id == container.id).count()
        dp_count = db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "container", CfgDataPoint.owner_id == container.id).count()
        if (eq_count or dp_count) and not force:
            raise ValueError("Container has dependent resources; pass force=true to delete")
        if force:
            equipment_ids = [row[0] for row in db.query(CfgEquipment.id).filter(CfgEquipment.container_id == container.id).all()]
            db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "container", CfgDataPoint.owner_id == container.id).delete(synchronize_session=False)
            if equipment_ids:
                db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "equipment", CfgDataPoint.owner_id.in_(equipment_ids)).delete(synchronize_session=False)
        db.delete(container)
        db.commit()

    # ---------------- Equipment ----------------
    def list_equipment(self, db: Session, container_id: int) -> list[CfgEquipment]:
        return db.query(CfgEquipment).filter(CfgEquipment.container_id == container_id).order_by(CfgEquipment.id.asc()).all()

    def get_equipment(self, db: Session, equipment_id: int) -> Optional[CfgEquipment]:
        return db.query(CfgEquipment).filter(CfgEquipment.id == equipment_id).one_or_none()

    def create_equipment(self, db: Session, *, container: CfgContainer, name: str, type_: str, user_id: Optional[int]) -> CfgEquipment:
        name = validate_name(name)
        type_ = validate_name(type_, field="type")
        if db.query(CfgEquipment).filter(CfgEquipment.container_id == container.id, CfgEquipment.name == name).first():
            raise ValueError("Equipment name already exists in container")
        e = CfgEquipment(
            container_id=container.id,
            name=name,
            type=type_,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return e

    def update_equipment(self, db: Session, equipment: CfgEquipment, *, patch: Dict[str, Any], user_id: Optional[int]) -> CfgEquipment:
        if "name" in patch and patch["name"] is not None:
            name = validate_name(str(patch["name"]))
            if name != equipment.name and db.query(CfgEquipment).filter(CfgEquipment.container_id == equipment.container_id, CfgEquipment.name == name).first():
                raise ValueError("Equipment name already exists in container")
            equipment.name = name
        if "type" in patch and patch["type"] is not None:
            equipment.type = validate_name(str(patch["type"]), field="type")
        equipment.updated_by_user_id = user_id
        db.add(equipment)
        db.commit()
        db.refresh(equipment)
        return equipment

    def delete_equipment(self, db: Session, equipment: CfgEquipment, *, force: bool) -> None:
        dp_count = db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "equipment", CfgDataPoint.owner_id == equipment.id).count()
        if dp_count and not force:
            raise ValueError("Equipment has dependent resources; pass force=true to delete")
        if force:
            db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == "equipment", CfgDataPoint.owner_id == equipment.id).delete()
        db.delete(equipment)
        db.commit()

    # ---------------- Data Points ----------------
    def list_data_points(self, db: Session, *, owner_type: str, owner_id: int) -> list[CfgDataPoint]:
        return (
            db.query(CfgDataPoint)
            .filter(CfgDataPoint.owner_type == owner_type, CfgDataPoint.owner_id == owner_id)
            .order_by(CfgDataPoint.id.asc())
            .all()
        )

    def get_data_point(self, db: Session, data_point_id: int) -> Optional[CfgDataPoint]:
        return db.query(CfgDataPoint).filter(CfgDataPoint.id == data_point_id).one_or_none()

    def create_data_point(
        self,
        db: Session,
        *,
        owner_type: str,
        owner_id: int,
        label: str,
        description: Optional[str],
        category: str,
        type_: str,
        address: str,
        bit_labels: Optional[Dict[int, str]],
        user_id: Optional[int],
    ) -> CfgDataPoint:
        if owner_type not in ("plc", "container", "equipment"):
            raise ValueError("invalid owner_type")

        label = validate_label(label)
        if category not in ("read", "write"):
            raise ValueError("invalid category")
        if type_ not in ("INTEGER", "DIGITAL", "REAL"):
            raise ValueError("invalid type")
        address = validate_address(address)

        if db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == owner_type, CfgDataPoint.owner_id == owner_id, CfgDataPoint.label == label).first():
            raise ValueError("Data point label already exists for owner")

        dp = CfgDataPoint(
            owner_type=owner_type,
            owner_id=owner_id,
            label=label,
            description=(description.strip() if description else None),
            category=category,
            type=type_,
            address=address,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )

        if type_ == "DIGITAL":
            bits = _dedupe_bits({int(k): str(v) for k, v in (bit_labels or {}).items()})
            for bit, lbl in bits.items():
                if bit < 0 or bit > self.digital_bit_max:
                    raise ValueError(f"bit must be between 0 and {self.digital_bit_max}")
                if len(lbl) > 200:
                    raise ValueError("bit label too long")
                dp.bits.append(CfgDataPointBit(bit=bit, label=lbl))
        else:
            if bit_labels:
                raise ValueError("bitLabels only allowed when type is DIGITAL")

        db.add(dp)
        db.commit()
        db.refresh(dp)
        return dp

    def update_data_point(self, db: Session, dp: CfgDataPoint, *, patch: Dict[str, Any], user_id: Optional[int]) -> CfgDataPoint:
        if "label" in patch and patch["label"] is not None:
            label = validate_label(str(patch["label"]))
            if label != dp.label and db.query(CfgDataPoint).filter(CfgDataPoint.owner_type == dp.owner_type, CfgDataPoint.owner_id == dp.owner_id, CfgDataPoint.label == label).first():
                raise ValueError("Data point label already exists for owner")
            dp.label = label
        if "description" in patch:
            desc = patch.get("description")
            dp.description = (str(desc).strip() if desc is not None else None)
        if "category" in patch and patch["category"] is not None:
            category = str(patch["category"]).strip().lower()
            if category not in ("read", "write"):
                raise ValueError("invalid category")
            dp.category = category
        if "type" in patch and patch["type"] is not None:
            type_ = str(patch["type"]).strip().upper()
            if type_ not in ("INTEGER", "DIGITAL", "REAL"):
                raise ValueError("invalid type")
            dp.type = type_
        if "address" in patch and patch["address"] is not None:
            dp.address = validate_address(str(patch["address"]))

        # Bit labels are treated as replace-on-write.
        if "bitLabels" in patch:
            bit_labels = patch.get("bitLabels")
            if dp.type != "DIGITAL":
                if bit_labels:
                    raise ValueError("bitLabels only allowed when type is DIGITAL")
                dp.bits = []
            else:
                bits_map: Dict[int, str] = {}
                if isinstance(bit_labels, dict):
                    for k, v in bit_labels.items():
                        bits_map[int(k)] = str(v)
                bits = _dedupe_bits(bits_map)
                dp.bits = []
                for bit, lbl in bits.items():
                    if bit < 0 or bit > self.digital_bit_max:
                        raise ValueError(f"bit must be between 0 and {self.digital_bit_max}")
                    if len(lbl) > 200:
                        raise ValueError("bit label too long")
                    dp.bits.append(CfgDataPointBit(bit=bit, label=lbl))

        dp.updated_by_user_id = user_id
        db.add(dp)
        db.commit()
        db.refresh(dp)
        return dp

    def delete_data_point(self, db: Session, dp: CfgDataPoint) -> None:
        db.delete(dp)
        db.commit()