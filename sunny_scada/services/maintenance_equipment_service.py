from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import Equipment


ALLOWED_ASSET_CATEGORIES = {"refrigeration", "processing", "utility", "cip"}
ALLOWED_CRITICALITY = {"A", "B", "C"}
ALLOWED_SPARES_CLASS = {"fast_moving", "standard", "long_lead"}
ALLOWED_SAFETY_CLASSIFICATION = {
    "pressure_vessel",
    "rotating",
    "electrical",
    "ammonia_exposure",
    "confined_space",
}


HIERARCHY_RULES: dict[str, dict[str, set[str]]] = {
    "refrigeration": {
        "compressor": {"refrigeration_system", "pack"},
        "receiver_hp": {"refrigeration_system"},
        "receiver_lp": {"refrigeration_system"},
        "evaporator": {"cold_room", "frozen_room"},
        "motor": {"compressor", "machine"},
        "vfd": {"compressor", "machine"},
    },
    "processing": {
        "machine": {"line"},
        "gearbox": {"machine"},
        "bearing": {"machine"},
        "vibrator": {"machine"},
        "sieve": {"machine"},
        "conveyor": {"machine"},
    },
}


def _norm_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_asset(value: Any) -> Optional[str]:
    text = _norm_text(value)
    return text.lower() if text is not None else None


class MaintenanceEquipmentService:
    def _load_all(self, db: Session) -> list[Equipment]:
        return db.query(Equipment).order_by(Equipment.id.asc()).all()

    def validate_payload(
        self,
        db: Session,
        *,
        payload: dict[str, Any],
        equipment_id: Optional[int] = None,
    ) -> dict[str, Any]:
        cleaned = dict(payload or {})

        category = _norm_asset(cleaned.get("asset_category"))
        asset_type = _norm_asset(cleaned.get("asset_type"))

        if category is not None and category not in ALLOWED_ASSET_CATEGORIES:
            raise ValueError("asset_category must be one of refrigeration, processing, utility, cip")

        criticality = _norm_text(cleaned.get("criticality"))
        if criticality is None:
            criticality = "B"
        if criticality not in ALLOWED_CRITICALITY:
            raise ValueError("criticality must be one of A, B, C")

        spares_class = _norm_asset(cleaned.get("spares_class"))
        if spares_class is None:
            spares_class = "standard"
        if spares_class not in ALLOWED_SPARES_CLASS:
            raise ValueError("spares_class must be one of fast_moving, standard, long_lead")

        duty_cycle = cleaned.get("duty_cycle_hours_per_day")
        if duty_cycle in ("", None):
            duty_cycle = None
        elif float(duty_cycle) < 0:
            raise ValueError("duty_cycle_hours_per_day must be >= 0")
        else:
            duty_cycle = float(duty_cycle)

        safety_raw = cleaned.get("safety_classification")
        if safety_raw in (None, ""):
            safety = []
        elif isinstance(safety_raw, list):
            safety = []
            for item in safety_raw:
                value = _norm_asset(item)
                if value is None:
                    continue
                if value not in ALLOWED_SAFETY_CLASSIFICATION:
                    raise ValueError(
                        "safety_classification contains invalid values; allowed: pressure_vessel, rotating, electrical, ammonia_exposure, confined_space"
                    )
                if value not in safety:
                    safety.append(value)
        else:
            raise ValueError("safety_classification must be an array of strings")

        parent_id_raw = cleaned.get("parent_id")
        parent_id: Optional[int]
        if parent_id_raw in (None, "", 0, "0"):
            parent_id = None
        else:
            parent_id = int(parent_id_raw)
            if equipment_id is not None and parent_id == int(equipment_id):
                raise ValueError("parent_id cannot be the same as equipment id")

        parent_row: Optional[Equipment] = None
        if parent_id is not None:
            parent_row = db.query(Equipment).filter(Equipment.id == int(parent_id)).one_or_none()
            if parent_row is None:
                raise ValueError("parent_id not found")

        if category and asset_type:
            allowed_parents = HIERARCHY_RULES.get(category, {}).get(asset_type)
            if allowed_parents is not None:
                if parent_row is None:
                    allowed_text = ", ".join(sorted(allowed_parents))
                    raise ValueError(f"asset_type '{asset_type}' in category '{category}' requires parent asset_type in {{{allowed_text}}}")
                parent_type = _norm_asset(parent_row.asset_type)
                if parent_type not in allowed_parents:
                    allowed_text = ", ".join(sorted(allowed_parents))
                    raise ValueError(f"invalid hierarchy: asset_type '{asset_type}' must have parent asset_type in {{{allowed_text}}}")

        cleaned["asset_category"] = category
        cleaned["asset_type"] = asset_type
        cleaned["criticality"] = criticality
        cleaned["spares_class"] = spares_class
        cleaned["duty_cycle_hours_per_day"] = duty_cycle
        cleaned["safety_classification"] = safety
        cleaned["parent_id"] = parent_id
        return cleaned

    def equipment_out(self, row: Equipment) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "equipment_code": str(row.equipment_code),
            "name": str(row.name),
            "location": row.location,
            "description": row.description,
            "vendor_id": row.vendor_id,
            "is_active": bool(row.is_active),
            "parent_id": row.parent_id,
            "asset_category": row.asset_category,
            "asset_type": row.asset_type,
            "criticality": row.criticality,
            "duty_cycle_hours_per_day": row.duty_cycle_hours_per_day,
            "spares_class": row.spares_class,
            "safety_classification": list(row.safety_classification or []),
            "meta": row.meta or {},
        }

    def build_tree(self, db: Session, *, root_id: Optional[int] = None) -> list[dict[str, Any]]:
        rows = self._load_all(db)
        node_map: dict[int, dict[str, Any]] = {}
        for row in rows:
            node_map[int(row.id)] = {
                "id": int(row.id),
                "name": str(row.name),
                "equipment_code": str(row.equipment_code),
                "asset_type": row.asset_type,
                "criticality": row.criticality,
                "children": [],
            }

        roots: list[dict[str, Any]] = []
        for row in rows:
            node = node_map[int(row.id)]
            parent_id = int(row.parent_id) if row.parent_id is not None else None
            if parent_id is not None and parent_id in node_map:
                node_map[parent_id]["children"].append(node)
            else:
                roots.append(node)

        for node in node_map.values():
            node["children"] = sorted(node["children"], key=lambda x: (str(x.get("name") or ""), int(x["id"])))
        roots = sorted(roots, key=lambda x: (str(x.get("name") or ""), int(x["id"])))

        if root_id is None:
            return roots
        root = node_map.get(int(root_id))
        return [root] if root is not None else []

    def equipment_path(self, db: Session, *, equipment_id: int) -> list[dict[str, Any]]:
        rows = self._load_all(db)
        row_map = {int(row.id): row for row in rows}
        if int(equipment_id) not in row_map:
            raise ValueError("equipment not found")

        visited: set[int] = set()
        path: list[dict[str, Any]] = []
        current_id: Optional[int] = int(equipment_id)
        while current_id is not None and current_id in row_map and current_id not in visited:
            visited.add(current_id)
            row = row_map[current_id]
            path.append({"id": int(row.id), "name": str(row.name), "asset_type": row.asset_type})
            current_id = int(row.parent_id) if row.parent_id is not None else None

        path.reverse()
        return path

    def descendants(self, db: Session, *, equipment_id: int) -> list[int]:
        rows = self._load_all(db)
        row_map = {int(row.id): row for row in rows}
        if int(equipment_id) not in row_map:
            raise ValueError("equipment not found")

        children_map: dict[int, list[int]] = {}
        for row in rows:
            if row.parent_id is None:
                continue
            parent = int(row.parent_id)
            children_map.setdefault(parent, []).append(int(row.id))

        result: list[int] = []
        stack = list(children_map.get(int(equipment_id), []))
        while stack:
            node_id = stack.pop()
            result.append(node_id)
            stack.extend(children_map.get(node_id, []))

        return sorted(set(result))
