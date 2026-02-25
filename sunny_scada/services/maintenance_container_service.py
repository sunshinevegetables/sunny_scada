from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import MaintenanceContainer
from sunny_scada.services.maintenance_equipment_service import (
    ALLOWED_ASSET_CATEGORIES,
    ALLOWED_CRITICALITY,
    ALLOWED_SAFETY_CLASSIFICATION,
    ALLOWED_SPARES_CLASS,
    HIERARCHY_RULES,
    MaintenanceEquipmentService,
    _norm_asset,
    _norm_text,
)


class MaintenanceContainerService(MaintenanceEquipmentService):
    def _load_all_containers(self, db: Session) -> list[MaintenanceContainer]:
        return db.query(MaintenanceContainer).order_by(MaintenanceContainer.id.asc()).all()

    def container_out(self, row: MaintenanceContainer) -> dict[str, Any]:
        return {
            "id": int(row.id),
            "container_code": str(row.container_code),
            "name": str(row.name),
            "location": row.location,
            "description": row.description,
            "parent_id": row.parent_id,
            "asset_category": row.asset_category,
            "asset_type": row.asset_type,
            "criticality": row.criticality,
            "duty_cycle_hours_per_day": row.duty_cycle_hours_per_day,
            "spares_class": row.spares_class,
            "safety_classification": list(row.safety_classification or []),
            "is_active": bool(row.is_active),
            "meta": row.meta or {},
        }

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
                raise ValueError("parent_id cannot be the same as container id")

        parent_row: Optional[MaintenanceContainer] = None
        if parent_id is not None:
            parent_row = db.query(MaintenanceContainer).filter(MaintenanceContainer.id == int(parent_id)).one_or_none()
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

    def build_tree(self, db: Session) -> list[dict[str, Any]]:
        rows = self._load_all_containers(db)
        node_map: dict[int, dict[str, Any]] = {}
        for row in rows:
            node_map[int(row.id)] = {
                "id": int(row.id),
                "name": str(row.name),
                "container_code": str(row.container_code),
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
        return roots
