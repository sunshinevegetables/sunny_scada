from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import (
    CfgPLC,
    CfgContainer,
    CfgEquipment,
    CfgContainerType,
    CfgEquipmentType,
    CfgDataPoint,
    CfgDataPointBit,
    CfgDataPointClass,
    CfgDataPointUnit,
    CfgDataPointGroup,
)


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,199}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,199}$")
_ADDR_RE = re.compile(r"^[A-Za-z0-9_:\.\/\[\]\(\)\-]+$")


def validate_meta_option_name(value: str, *, field: str = "name") -> str:
    """Validate names for datapoint meta option lists (class/unit/group).

    These names commonly contain symbols (e.g. "°C", "m³/h"). We keep validation
    permissive but safe (no control characters or angle brackets).
    """

    v = str(value).strip()
    if not v:
        raise ValueError(f"{field} is required")
    if len(v) > 200:
        raise ValueError(f"{field} too long")
    if any(ch in v for ch in ("\n", "\r", "\t")):
        raise ValueError(f"{field} contains invalid characters")
    if "<" in v or ">" in v:
        raise ValueError(f"{field} contains invalid characters")
    return v


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


def _normalize_bit_positions(
    *,
    digital_bit_max: int,
    bit_labels: Optional[Dict[int, str]] = None,
    bit_positions: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[int, Dict[str, Optional[str]]]:
    out: Dict[int, Dict[str, Optional[str]]] = {}

    if isinstance(bit_labels, dict):
        for k, v in bit_labels.items():
            bit = int(k)
            if bit < 0 or bit > digital_bit_max:
                raise ValueError(f"bit must be between 0 and {digital_bit_max}")
            label = str(v).strip()
            if not label:
                continue
            if len(label) > 200:
                raise ValueError("bit label too long")
            out[bit] = {"label": label, "class": None}

    if isinstance(bit_positions, dict):
        for k, payload in bit_positions.items():
            bit = int(k)
            if bit < 0 or bit > digital_bit_max:
                raise ValueError(f"bit must be between 0 and {digital_bit_max}")

            if isinstance(payload, dict):
                raw_label = payload.get("label")
                raw_class = payload.get("class")
            else:
                raw_label = payload
                raw_class = None

            label = str(raw_label).strip() if raw_label is not None else ""
            if not label:
                continue
            if len(label) > 200:
                raise ValueError("bit label too long")

            bit_class = None
            if raw_class is not None:
                cls = str(raw_class).strip()
                if cls:
                    if len(cls) > 100:
                        raise ValueError("bit class too long")
                    bit_class = cls

            out[bit] = {"label": label, "class": bit_class}

    return out


@dataclass
class SystemConfigService:
    digital_bit_max: int = 15

    # ---------------- PLC ----------------
    def list_plcs(self, db: Session) -> list[CfgPLC]:
        return db.query(CfgPLC).order_by(CfgPLC.id.asc()).all()

    def get_plc(self, db: Session, plc_id: int) -> Optional[CfgPLC]:
        return db.query(CfgPLC).filter(CfgPLC.id == plc_id).one_or_none()

    def create_plc(
        self,
        db: Session,
        *,
        name: str,
        ip: str,
        port: int,
        group_id: Optional[int] = None,
        user_id: Optional[int],
    ) -> CfgPLC:
        name = validate_name(name)
        ip = validate_ip_or_hostname(ip)
        if port < 1 or port > 65535:
            raise ValueError("port must be between 1 and 65535")

        if group_id is not None and not self.get_datapoint_group(db, int(group_id)):
            raise ValueError("group not found")

        if db.query(CfgPLC).filter(CfgPLC.name == name).first():
            raise ValueError("PLC name already exists")

        plc = CfgPLC(
            name=name,
            ip=ip,
            port=port,
            group_id=(int(group_id) if group_id is not None else None),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
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
        if "groupId" in patch:
            gid = patch.get("groupId")
            if gid is None:
                plc.group_id = None
            else:
                if not self.get_datapoint_group(db, int(gid)):
                    raise ValueError("group not found")
                plc.group_id = int(gid)

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

    def create_container(self, db: Session, *, plc: CfgPLC, name: str, type_: str, group_id: Optional[int] = None, user_id: Optional[int] = None) -> CfgContainer:
        name = validate_name(name)
        type_ = validate_name(type_, field="type")
        if db.query(CfgContainer).filter(CfgContainer.plc_id == plc.id, CfgContainer.name == name).first():
            raise ValueError("Container name already exists in PLC")
        if group_id is None and getattr(plc, "group_id", None) is not None:
            group_id = int(plc.group_id)
        if group_id is not None and not self.get_datapoint_group(db, int(group_id)):
            raise ValueError("group not found")

        c = CfgContainer(
            plc_id=plc.id,
            name=name,
            type=type_,
            group_id=(int(group_id) if group_id is not None else None),
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
        # group patch support
        if "groupId" in patch:
            gid = patch.get("groupId")
            if gid is None:
                container.group_id = None
            else:
                if not self.get_datapoint_group(db, int(gid)):
                    raise ValueError("group not found")
                container.group_id = int(gid)
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

    def create_equipment(self, db: Session, *, container: CfgContainer, name: str, type_: str, group_id: Optional[int] = None, user_id: Optional[int] = None) -> CfgEquipment:
        name = validate_name(name)
        type_ = validate_name(type_, field="type")
        if db.query(CfgEquipment).filter(CfgEquipment.container_id == container.id, CfgEquipment.name == name).first():
            raise ValueError("Equipment name already exists in container")
        if group_id is None and getattr(container, "group_id", None) is not None:
            group_id = int(container.group_id)
        if group_id is None:
            parent_plc = self.get_plc(db, int(container.plc_id))
            if parent_plc is not None and getattr(parent_plc, "group_id", None) is not None:
                group_id = int(parent_plc.group_id)
        if group_id is not None and not self.get_datapoint_group(db, int(group_id)):
            raise ValueError("group not found")

        e = CfgEquipment(
            container_id=container.id,
            name=name,
            type=type_,
            group_id=(int(group_id) if group_id is not None else None),
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
        # group patch support
        if "groupId" in patch:
            gid = patch.get("groupId")
            if gid is None:
                equipment.group_id = None
            else:
                if not self.get_datapoint_group(db, int(gid)):
                    raise ValueError("group not found")
                equipment.group_id = int(gid)
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

    # ---------------- Data Point Meta (Classes / Units / Groups) ----------------

    def list_datapoint_classes(self, db: Session) -> list[CfgDataPointClass]:
        return db.query(CfgDataPointClass).order_by(CfgDataPointClass.id.asc()).all()

    def get_datapoint_class(self, db: Session, class_id: int) -> Optional[CfgDataPointClass]:
        return db.query(CfgDataPointClass).filter(CfgDataPointClass.id == class_id).one_or_none()

    def create_datapoint_class(
        self, db: Session, *, name: str, description: Optional[str], user_id: Optional[int]
    ) -> CfgDataPointClass:
        name = validate_meta_option_name(name, field="name")
        if db.query(CfgDataPointClass).filter(CfgDataPointClass.name == name).first():
            raise ValueError("class name already exists")

        obj = CfgDataPointClass(
            name=name,
            description=(description.strip() if description else None),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update_datapoint_class(
        self, db: Session, obj: CfgDataPointClass, *, patch: Dict[str, Any], user_id: Optional[int]
    ) -> CfgDataPointClass:
        if "name" in patch and patch["name"] is not None:
            name = validate_meta_option_name(str(patch["name"]), field="name")
            if name != obj.name and db.query(CfgDataPointClass).filter(CfgDataPointClass.name == name).first():
                raise ValueError("class name already exists")
            obj.name = name
        if "description" in patch:
            desc = patch.get("description")
            obj.description = (str(desc).strip() if desc is not None else None)
        obj.updated_by_user_id = user_id
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def delete_datapoint_class(self, db: Session, obj: CfgDataPointClass) -> None:
        in_use = db.query(CfgDataPoint.id).filter(CfgDataPoint.class_id == obj.id).first()
        if in_use:
            raise ValueError("class is in use by datapoints")
        db.delete(obj)
        db.commit()

    def list_datapoint_units(self, db: Session) -> list[CfgDataPointUnit]:
        return db.query(CfgDataPointUnit).order_by(CfgDataPointUnit.id.asc()).all()

    def get_datapoint_unit(self, db: Session, unit_id: int) -> Optional[CfgDataPointUnit]:
        return db.query(CfgDataPointUnit).filter(CfgDataPointUnit.id == unit_id).one_or_none()

    def create_datapoint_unit(
        self, db: Session, *, name: str, description: Optional[str], user_id: Optional[int]
    ) -> CfgDataPointUnit:
        name = validate_meta_option_name(name, field="name")
        if db.query(CfgDataPointUnit).filter(CfgDataPointUnit.name == name).first():
            raise ValueError("unit name already exists")

        obj = CfgDataPointUnit(
            name=name,
            description=(description.strip() if description else None),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update_datapoint_unit(
        self, db: Session, obj: CfgDataPointUnit, *, patch: Dict[str, Any], user_id: Optional[int]
    ) -> CfgDataPointUnit:
        if "name" in patch and patch["name"] is not None:
            name = validate_meta_option_name(str(patch["name"]), field="name")
            if name != obj.name and db.query(CfgDataPointUnit).filter(CfgDataPointUnit.name == name).first():
                raise ValueError("unit name already exists")
            obj.name = name
        if "description" in patch:
            desc = patch.get("description")
            obj.description = (str(desc).strip() if desc is not None else None)
        obj.updated_by_user_id = user_id
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def delete_datapoint_unit(self, db: Session, obj: CfgDataPointUnit) -> None:
        in_use = db.query(CfgDataPoint.id).filter(CfgDataPoint.unit_id == obj.id).first()
        if in_use:
            raise ValueError("unit is in use by datapoints")
        db.delete(obj)
        db.commit()

    def list_datapoint_groups(self, db: Session) -> list[CfgDataPointGroup]:
        return db.query(CfgDataPointGroup).order_by(CfgDataPointGroup.id.asc()).all()

    def get_datapoint_group(self, db: Session, group_id: int) -> Optional[CfgDataPointGroup]:
        return db.query(CfgDataPointGroup).filter(CfgDataPointGroup.id == group_id).one_or_none()

    def create_datapoint_group(
        self, db: Session, *, name: str, description: Optional[str], user_id: Optional[int]
    ) -> CfgDataPointGroup:
        name = validate_meta_option_name(name, field="name")
        if db.query(CfgDataPointGroup).filter(CfgDataPointGroup.name == name).first():
            raise ValueError("group name already exists")

        obj = CfgDataPointGroup(
            name=name,
            description=(description.strip() if description else None),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update_datapoint_group(
        self, db: Session, obj: CfgDataPointGroup, *, patch: Dict[str, Any], user_id: Optional[int]
    ) -> CfgDataPointGroup:
        if "name" in patch and patch["name"] is not None:
            name = validate_meta_option_name(str(patch["name"]), field="name")
            if name != obj.name and db.query(CfgDataPointGroup).filter(CfgDataPointGroup.name == name).first():
                raise ValueError("group name already exists")
            obj.name = name
        if "description" in patch:
            desc = patch.get("description")
            obj.description = (str(desc).strip() if desc is not None else None)
        obj.updated_by_user_id = user_id
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def delete_datapoint_group(self, db: Session, obj: CfgDataPointGroup) -> None:
        in_use = db.query(CfgDataPoint.id).filter(CfgDataPoint.group_id == obj.id).first()
        if not in_use:
            in_use = db.query(CfgEquipment.id).filter(CfgEquipment.group_id == obj.id).first()
        if not in_use:
            in_use = db.query(CfgContainer.id).filter(CfgContainer.group_id == obj.id).first()
        if not in_use:
            in_use = db.query(CfgPLC.id).filter(CfgPLC.group_id == obj.id).first()
        if in_use:
            raise ValueError("group is in use by configuration resources")
        db.delete(obj)
        db.commit()

    # ---------------- Container Type Meta ----------------

    def list_container_types(self, db: Session) -> list[CfgContainerType]:
        return db.query(CfgContainerType).order_by(CfgContainerType.id.asc()).all()

    def get_container_type(self, db: Session, type_id: int) -> Optional[CfgContainerType]:
        return db.query(CfgContainerType).filter(CfgContainerType.id == type_id).one_or_none()

    def create_container_type(
        self, db: Session, *, name: str, description: Optional[str], user_id: Optional[int]
    ) -> CfgContainerType:
        name = validate_meta_option_name(name, field="name")
        if db.query(CfgContainerType).filter(CfgContainerType.name == name).first():
            raise ValueError("container type name already exists")

        obj = CfgContainerType(
            name=name,
            description=(description.strip() if description else None),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update_container_type(
        self, db: Session, obj: CfgContainerType, *, patch: Dict[str, Any], user_id: Optional[int]
    ) -> CfgContainerType:
        old_name = obj.name
        if "name" in patch and patch["name"] is not None:
            name = validate_meta_option_name(str(patch["name"]), field="name")
            if name != obj.name and db.query(CfgContainerType).filter(CfgContainerType.name == name).first():
                raise ValueError("container type name already exists")
            obj.name = name
        if "description" in patch:
            desc = patch.get("description")
            obj.description = (str(desc).strip() if desc is not None else None)
        obj.updated_by_user_id = user_id
        db.add(obj)

        # Keep existing containers consistent when type option is renamed.
        if old_name != obj.name:
            db.query(CfgContainer).filter(CfgContainer.type == old_name).update(
                {CfgContainer.type: obj.name}, synchronize_session=False
            )

        db.commit()
        db.refresh(obj)
        return obj

    def delete_container_type(self, db: Session, obj: CfgContainerType) -> None:
        in_use = db.query(CfgContainer.id).filter(CfgContainer.type == obj.name).first()
        if in_use:
            raise ValueError("container type is in use by containers")
        db.delete(obj)
        db.commit()

    # ---------------- Equipment Type Meta ----------------

    def list_equipment_types(self, db: Session) -> list[CfgEquipmentType]:
        return db.query(CfgEquipmentType).order_by(CfgEquipmentType.id.asc()).all()

    def get_equipment_type(self, db: Session, type_id: int) -> Optional[CfgEquipmentType]:
        return db.query(CfgEquipmentType).filter(CfgEquipmentType.id == type_id).one_or_none()

    def create_equipment_type(
        self, db: Session, *, name: str, description: Optional[str], user_id: Optional[int]
    ) -> CfgEquipmentType:
        name = validate_meta_option_name(name, field="name")
        if db.query(CfgEquipmentType).filter(CfgEquipmentType.name == name).first():
            raise ValueError("equipment type name already exists")

        obj = CfgEquipmentType(
            name=name,
            description=(description.strip() if description else None),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update_equipment_type(
        self, db: Session, obj: CfgEquipmentType, *, patch: Dict[str, Any], user_id: Optional[int]
    ) -> CfgEquipmentType:
        old_name = obj.name
        if "name" in patch and patch["name"] is not None:
            name = validate_meta_option_name(str(patch["name"]), field="name")
            if name != obj.name and db.query(CfgEquipmentType).filter(CfgEquipmentType.name == name).first():
                raise ValueError("equipment type name already exists")
            obj.name = name
        if "description" in patch:
            desc = patch.get("description")
            obj.description = (str(desc).strip() if desc is not None else None)
        obj.updated_by_user_id = user_id
        db.add(obj)

        if old_name != obj.name:
            db.query(CfgEquipment).filter(CfgEquipment.type == old_name).update(
                {CfgEquipment.type: obj.name}, synchronize_session=False
            )

        db.commit()
        db.refresh(obj)
        return obj

    def delete_equipment_type(self, db: Session, obj: CfgEquipmentType) -> None:
        in_use = db.query(CfgEquipment.id).filter(CfgEquipment.type == obj.name).first()
        if in_use:
            raise ValueError("equipment type is in use by equipment")
        db.delete(obj)
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

    def find_write_data_point_by_equipment(
        self,
        db: Session,
        *,
        plc_name: str,
        equipment_label: str,
        command_tag: str,
        equipment_id: Optional[int] = None,
    ) -> Optional[CfgDataPoint]:
        """Find a write datapoint by PLC name, equipment label, and command tag (label).

        Returns the CfgDataPoint if found, otherwise None.
        """
        plc = db.query(CfgPLC).filter(CfgPLC.name == plc_name).one_or_none()
        if not plc:
            return None
        
        equipment_query = (
            db.query(CfgEquipment)
            .join(CfgContainer, CfgEquipment.container_id == CfgContainer.id)
            .filter(CfgContainer.plc_id == plc.id)
        )

        if equipment_id is not None:
            equipment_query = equipment_query.filter(CfgEquipment.id == int(equipment_id))
        else:
            equipment_query = equipment_query.filter(CfgEquipment.name == equipment_label)

        equipment_rows = equipment_query.order_by(CfgEquipment.id.asc()).all()
        if not equipment_rows:
            return None
        if len(equipment_rows) > 1:
            raise ValueError(
                f"Multiple equipment matched label '{equipment_label}' in PLC '{plc_name}'. Provide equipmentId to disambiguate."
            )

        equipment = equipment_rows[0]

        # Find the write datapoint whose owner is this equipment & label is the command tag
        rows = (
            db.query(CfgDataPoint)
            .filter(
                CfgDataPoint.owner_type == "equipment",
                CfgDataPoint.owner_id == equipment.id,
                CfgDataPoint.category == "write",
                CfgDataPoint.label == command_tag,
            )
            .order_by(CfgDataPoint.id.asc())
            .all()
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(
                f"Multiple write datapoints matched tag '{command_tag}' for equipment '{equipment.name}'."
            )
        return rows[0]

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
        group_id: Optional[int],
        class_id: Optional[int],
        unit_id: Optional[int],
        multiplier: Optional[int],
        bit_labels: Optional[Dict[int, str]],
        bit_positions: Optional[Dict[int, Dict[str, Any]]] = None,
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

        # Validate meta fields
        if type_ == "DIGITAL" and (class_id is not None or unit_id is not None):
            raise ValueError("class/unit are only allowed for REAL or INTEGER datapoints")

        # If group_id not provided, inherit from owner hierarchy plc -> container -> equipment -> datapoint
        if group_id is None:
            if owner_type == "plc":
                parent = self.get_plc(db, int(owner_id))
                if parent is not None and getattr(parent, "group_id", None) is not None:
                    group_id = int(parent.group_id)
            if owner_type == "container":
                parent = self.get_container(db, int(owner_id))
                if parent is not None and getattr(parent, "group_id", None) is not None:
                    group_id = int(parent.group_id)
                if group_id is None and parent is not None:
                    plc = self.get_plc(db, int(parent.plc_id))
                    if plc is not None and getattr(plc, "group_id", None) is not None:
                        group_id = int(plc.group_id)
            elif owner_type == "equipment":
                parent = self.get_equipment(db, int(owner_id))
                if parent is not None and getattr(parent, "group_id", None) is not None:
                    group_id = int(parent.group_id)
                if group_id is None and parent is not None:
                    container = self.get_container(db, int(parent.container_id))
                    if container is not None and getattr(container, "group_id", None) is not None:
                        group_id = int(container.group_id)
                    elif container is not None:
                        plc = self.get_plc(db, int(container.plc_id))
                        if plc is not None and getattr(plc, "group_id", None) is not None:
                            group_id = int(plc.group_id)

        if group_id is not None and not self.get_datapoint_group(db, int(group_id)):
            raise ValueError("group not found")
        if class_id is not None and not self.get_datapoint_class(db, int(class_id)):
            raise ValueError("class not found")
        if unit_id is not None and not self.get_datapoint_unit(db, int(unit_id)):
            raise ValueError("unit not found")

        mult = 1 if multiplier is None else int(multiplier)

        dp = CfgDataPoint(
            owner_type=owner_type,
            owner_id=owner_id,
            label=label,
            description=(description.strip() if description else None),
            category=category,
            type=type_,
            address=address,
            group_id=(int(group_id) if group_id is not None else None),
            class_id=(int(class_id) if class_id is not None else None),
            unit_id=(int(unit_id) if unit_id is not None else None),
            multiplier=mult,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )

        if type_ == "DIGITAL":
            bits = _normalize_bit_positions(
                digital_bit_max=self.digital_bit_max,
                bit_labels=bit_labels,
                bit_positions=bit_positions,
            )
            for bit, payload in bits.items():
                dp.bits.append(
                    CfgDataPointBit(
                        bit=bit,
                        label=payload.get("label") or "",
                        bit_class=payload.get("class"),
                    )
                )
        else:
            if bit_labels or bit_positions:
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

        # Meta fields
        if "multiplier" in patch:
            mult = patch.get("multiplier")
            dp.multiplier = 1 if mult is None else int(mult)

        if "groupId" in patch:
            gid = patch.get("groupId")
            if gid is None:
                dp.group_id = None
            else:
                if not self.get_datapoint_group(db, int(gid)):
                    raise ValueError("group not found")
                dp.group_id = int(gid)

        # If type has been changed to DIGITAL, auto-clear class/unit
        if "type" in patch and dp.type == "DIGITAL":
            dp.class_id = None
            dp.unit_id = None

        if "classId" in patch:
            cid = patch.get("classId")
            if dp.type == "DIGITAL" and cid is not None:
                raise ValueError("class only allowed for REAL or INTEGER datapoints")
            if cid is None:
                dp.class_id = None
            else:
                if not self.get_datapoint_class(db, int(cid)):
                    raise ValueError("class not found")
                dp.class_id = int(cid)

        if "unitId" in patch:
            uid = patch.get("unitId")
            if dp.type == "DIGITAL" and uid is not None:
                raise ValueError("unit only allowed for REAL or INTEGER datapoints")
            if uid is None:
                dp.unit_id = None
            else:
                if not self.get_datapoint_unit(db, int(uid)):
                    raise ValueError("unit not found")
                dp.unit_id = int(uid)

        # Guard against existing invalid state (e.g., older rows) when type is DIGITAL.
        if dp.type == "DIGITAL" and (dp.class_id is not None or dp.unit_id is not None):
            dp.class_id = None
            dp.unit_id = None

        # Bit labels/positions are treated as replace-on-write.
        if ("bitLabels" in patch) or ("bitPositions" in patch):
            bit_labels = patch.get("bitLabels")
            bit_positions = patch.get("bitPositions")
            if dp.type != "DIGITAL":
                if bit_labels or bit_positions:
                    raise ValueError("bitLabels only allowed when type is DIGITAL")
                dp.bits = []
            else:
                bits = _normalize_bit_positions(
                    digital_bit_max=self.digital_bit_max,
                    bit_labels=bit_labels,
                    bit_positions=bit_positions,
                )
                # Replace-on-write needs delete-before-insert ordering to avoid
                # UNIQUE(data_point_id, bit) conflicts during flush.
                dp.bits = []
                db.flush()
                for bit, payload in bits.items():
                    dp.bits.append(
                        CfgDataPointBit(
                            bit=bit,
                            label=payload.get("label") or "",
                            bit_class=payload.get("class"),
                        )
                    )

        dp.updated_by_user_id = user_id
        db.add(dp)
        db.commit()
        db.refresh(dp)
        return dp

    def delete_data_point(self, db: Session, dp: CfgDataPoint) -> None:
        db.delete(dp)
        db.commit()