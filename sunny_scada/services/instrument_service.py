from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from sunny_scada.db.models import (
    CfgDataPoint,
    Equipment,
    Instrument,
    InstrumentAttachment,
    InstrumentCalibration,
    InstrumentDataPoint,
    InstrumentSpareMap,
    SparePart,
    Vendor,
)
from sunny_scada.services.datapoint_identity import make_canonical_datapoint_key


_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*]')


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", str(name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "attachment.bin"


def _default_attachment_storage_path(instrument_id: int, filename: str) -> str:
    return f"static/uploads/instruments/{int(instrument_id)}/{_safe_filename(filename)}"


class InstrumentService:
    def _equipment_summary(self, equipment: Optional[Equipment]) -> Optional[dict[str, Any]]:
        if not equipment:
            return None
        return {
            "id": int(equipment.id),
            "equipment_code": str(equipment.equipment_code),
            "name": str(equipment.name),
            "location": equipment.location,
            "is_active": bool(equipment.is_active),
        }

    def _vendor_summary(self, vendor: Optional[Vendor]) -> Optional[dict[str, Any]]:
        if not vendor:
            return None
        return {
            "id": int(vendor.id),
            "name": str(vendor.name),
            "phone": vendor.phone,
            "email": vendor.email,
        }

    def _mapped_datapoints_out(self, instrument: Instrument) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for mapping in sorted(instrument.datapoints or [], key=lambda x: (int(x.cfg_data_point_id), str(x.role))):
            dp = mapping.cfg_data_point
            out.append(
                {
                    "cfg_data_point_id": int(mapping.cfg_data_point_id),
                    "datapoint_key": (make_canonical_datapoint_key(int(dp.id)) if dp is not None else None),
                    "label": (str(dp.label) if dp is not None else None),
                    "role": str(mapping.role),
                }
            )
        return out

    def _instrument_out(self, instrument: Instrument) -> dict[str, Any]:
        return {
            "id": int(instrument.id),
            "label": str(instrument.label),
            "status": str(instrument.status),
            "instrument_type": instrument.instrument_type,
            "model": instrument.model,
            "serial_number": instrument.serial_number,
            "location": instrument.location,
            "installed_at": instrument.installed_at,
            "notes": instrument.notes,
            "meta": instrument.meta or {},
            "equipment_id": instrument.equipment_id,
            "vendor_id": instrument.vendor_id,
            "equipment": self._equipment_summary(instrument.equipment),
            "vendor": self._vendor_summary(instrument.vendor),
            "mapped_datapoints": self._mapped_datapoints_out(instrument),
        }

    def _instrument_detail_out(self, instrument: Instrument) -> dict[str, Any]:
        base = self._instrument_out(instrument)

        recommended_spares: list[dict[str, Any]] = []
        current_stock_levels: list[dict[str, Any]] = []
        seen_part_ids: set[int] = set()

        for mapping in sorted(instrument.spare_map or [], key=lambda x: int(getattr(x, "id", 0))):
            part = mapping.part
            if part is None:
                continue

            on_hand = int(part.quantity_on_hand or 0)
            min_stock = int(part.min_stock or 0)
            reorder_required = on_hand <= min_stock

            recommended_spares.append(
                {
                    "map_id": int(mapping.id),
                    "spare_part_id": int(part.id),
                    "part_code": str(part.part_code),
                    "name": str(part.name),
                    "unit": part.unit,
                    "qty_per_replacement": int(mapping.qty_required or 0),
                    "current_stock": on_hand,
                    "min_stock": min_stock,
                    "reorder_required": bool(reorder_required),
                }
            )

            if int(part.id) not in seen_part_ids:
                current_stock_levels.append(
                    {
                        "spare_part_id": int(part.id),
                        "part_code": str(part.part_code),
                        "name": str(part.name),
                        "unit": part.unit,
                        "quantity_on_hand": on_hand,
                        "min_stock": min_stock,
                        "reorder_required": bool(reorder_required),
                    }
                )
                seen_part_ids.add(int(part.id))

        base["recommended_spares"] = recommended_spares
        base["current_stock_levels"] = current_stock_levels
        return base

    def _load_instrument(self, db: Session, instrument_id: int) -> Optional[Instrument]:
        return (
            db.query(Instrument)
            .options(
                selectinload(Instrument.equipment),
                selectinload(Instrument.vendor),
                selectinload(Instrument.datapoints).selectinload(InstrumentDataPoint.cfg_data_point),
                selectinload(Instrument.spare_map).selectinload(InstrumentSpareMap.part),
            )
            .filter(Instrument.id == int(instrument_id))
            .one_or_none()
        )

    def list_instruments(
        self,
        db: Session,
        *,
        equipment_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        status: Optional[str] = None,
        instrument_type: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        query = db.query(Instrument).options(
            selectinload(Instrument.equipment),
            selectinload(Instrument.vendor),
            selectinload(Instrument.datapoints).selectinload(InstrumentDataPoint.cfg_data_point),
        )
        if equipment_id is not None:
            query = query.filter(Instrument.equipment_id == int(equipment_id))
        if vendor_id is not None:
            query = query.filter(Instrument.vendor_id == int(vendor_id))
        if status is not None:
            query = query.filter(Instrument.status == str(status).strip())
        if instrument_type is not None:
            query = query.filter(Instrument.instrument_type == str(instrument_type).strip())
        if q is not None and str(q).strip():
            search = f"%{str(q).strip()}%"
            query = query.filter(
                or_(
                    Instrument.label.ilike(search),
                    Instrument.model.ilike(search),
                    Instrument.serial_number.ilike(search),
                    Instrument.location.ilike(search),
                    Instrument.instrument_type.ilike(search),
                )
            )
        rows = query.order_by(Instrument.id.asc()).all()
        return [self._instrument_out(row) for row in rows]

    def get_instrument(self, db: Session, instrument_id: int) -> Optional[dict[str, Any]]:
        row = self._load_instrument(db, int(instrument_id))
        if row is None:
            return None
        return self._instrument_detail_out(row)

    def create_instrument(
        self,
        db: Session,
        *,
        label: str,
        status: str = "active",
        equipment_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        instrument_type: Optional[str] = None,
        model: Optional[str] = None,
        serial_number: Optional[str] = None,
        location: Optional[str] = None,
        installed_at: Optional[dt.datetime] = None,
        notes: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        label_v = str(label or "").strip()
        if not label_v:
            raise ValueError("label is required")

        status_v = str(status or "active").strip() or "active"

        if equipment_id is not None and db.query(Equipment.id).filter(Equipment.id == int(equipment_id)).one_or_none() is None:
            raise ValueError("equipment not found")
        if vendor_id is not None and db.query(Vendor.id).filter(Vendor.id == int(vendor_id)).one_or_none() is None:
            raise ValueError("vendor not found")

        row = Instrument(
            label=label_v,
            status=status_v,
            equipment_id=(int(equipment_id) if equipment_id is not None else None),
            vendor_id=(int(vendor_id) if vendor_id is not None else None),
            instrument_type=(str(instrument_type).strip() if instrument_type is not None else None),
            model=(str(model).strip() if model is not None else None),
            serial_number=(str(serial_number).strip() if serial_number is not None else None),
            location=(str(location).strip() if location is not None else None),
            installed_at=installed_at,
            notes=(str(notes).strip() if notes is not None else None),
            meta=(meta or {}),
        )

        try:
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()
            raise

        out = self.get_instrument(db, int(row.id))
        if out is None:
            raise ValueError("failed to load created instrument")
        return out

    def update_instrument(self, db: Session, instrument_id: int, *, patch: Dict[str, Any]) -> dict[str, Any]:
        row = db.query(Instrument).filter(Instrument.id == int(instrument_id)).one_or_none()
        if row is None:
            raise ValueError("instrument not found")

        if "label" in patch:
            label_v = str(patch.get("label") or "").strip()
            if not label_v:
                raise ValueError("label is required")
            row.label = label_v

        if "status" in patch and patch.get("status") is not None:
            status_v = str(patch.get("status")).strip()
            if not status_v:
                raise ValueError("status is required")
            row.status = status_v

        if "equipment_id" in patch:
            eq_id = patch.get("equipment_id")
            if eq_id is None:
                row.equipment_id = None
            else:
                eq_id_int = int(eq_id)
                if db.query(Equipment.id).filter(Equipment.id == eq_id_int).one_or_none() is None:
                    raise ValueError("equipment not found")
                row.equipment_id = eq_id_int

        if "vendor_id" in patch:
            v_id = patch.get("vendor_id")
            if v_id is None:
                row.vendor_id = None
            else:
                v_id_int = int(v_id)
                if db.query(Vendor.id).filter(Vendor.id == v_id_int).one_or_none() is None:
                    raise ValueError("vendor not found")
                row.vendor_id = v_id_int

        if "instrument_type" in patch:
            value = patch.get("instrument_type")
            row.instrument_type = (str(value).strip() if value is not None else None)
        if "model" in patch:
            value = patch.get("model")
            row.model = (str(value).strip() if value is not None else None)
        if "serial_number" in patch:
            value = patch.get("serial_number")
            row.serial_number = (str(value).strip() if value is not None else None)
        if "location" in patch:
            value = patch.get("location")
            row.location = (str(value).strip() if value is not None else None)
        if "installed_at" in patch:
            row.installed_at = patch.get("installed_at")
        if "notes" in patch:
            value = patch.get("notes")
            row.notes = (str(value).strip() if value is not None else None)
        if "meta" in patch:
            row.meta = dict(patch.get("meta") or {})

        try:
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()
            raise

        out = self.get_instrument(db, int(row.id))
        if out is None:
            raise ValueError("failed to load updated instrument")
        return out

    def delete_instrument(self, db: Session, instrument_id: int) -> bool:
        row = db.query(Instrument).filter(Instrument.id == int(instrument_id)).one_or_none()
        if row is None:
            return False
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True

    def link_datapoint(self, db: Session, *, instrument_id: int, cfg_data_point_id: int, role: str = "process") -> dict[str, Any]:
        instrument = db.query(Instrument).filter(Instrument.id == int(instrument_id)).one_or_none()
        if instrument is None:
            raise ValueError("instrument not found")

        datapoint = db.query(CfgDataPoint).filter(CfgDataPoint.id == int(cfg_data_point_id)).one_or_none()
        if datapoint is None:
            raise ValueError("cfg_data_point not found")

        role_v = str(role or "process").strip() or "process"
        exists = (
            db.query(InstrumentDataPoint.id)
            .filter(
                InstrumentDataPoint.instrument_id == int(instrument_id),
                InstrumentDataPoint.cfg_data_point_id == int(cfg_data_point_id),
                InstrumentDataPoint.role == role_v,
            )
            .one_or_none()
        )
        if exists is not None:
            raise ValueError("datapoint mapping already exists")

        mapping = InstrumentDataPoint(
            instrument_id=int(instrument_id),
            cfg_data_point_id=int(cfg_data_point_id),
            role=role_v,
        )

        try:
            db.add(mapping)
            db.commit()
        except Exception:
            db.rollback()
            raise

        return {
            "id": int(mapping.id),
            "cfg_data_point_id": int(mapping.cfg_data_point_id),
            "datapoint_key": make_canonical_datapoint_key(int(mapping.cfg_data_point_id)),
            "label": str(datapoint.label),
            "role": str(mapping.role),
        }

    def list_datapoint_mappings(self, db: Session, *, instrument_id: int) -> list[dict[str, Any]]:
        rows = (
            db.query(InstrumentDataPoint)
            .options(selectinload(InstrumentDataPoint.cfg_data_point))
            .filter(InstrumentDataPoint.instrument_id == int(instrument_id))
            .order_by(InstrumentDataPoint.id.asc())
            .all()
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            dp = row.cfg_data_point
            out.append(
                {
                    "id": int(row.id),
                    "instrument_id": int(row.instrument_id),
                    "cfg_data_point_id": int(row.cfg_data_point_id),
                    "datapoint_key": (make_canonical_datapoint_key(int(dp.id)) if dp is not None else None),
                    "label": (str(dp.label) if dp is not None else None),
                    "role": str(row.role),
                }
            )
        return out

    def delete_datapoint_mapping(self, db: Session, *, instrument_id: int, map_id: int) -> bool:
        row = (
            db.query(InstrumentDataPoint)
            .filter(
                InstrumentDataPoint.id == int(map_id),
                InstrumentDataPoint.instrument_id == int(instrument_id),
            )
            .one_or_none()
        )
        if row is None:
            return False
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True

    def unlink_datapoint(
        self,
        db: Session,
        *,
        instrument_id: int,
        cfg_data_point_id: int,
        role: Optional[str] = None,
    ) -> int:
        q = db.query(InstrumentDataPoint).filter(
            InstrumentDataPoint.instrument_id == int(instrument_id),
            InstrumentDataPoint.cfg_data_point_id == int(cfg_data_point_id),
        )
        if role is not None:
            q = q.filter(InstrumentDataPoint.role == str(role).strip())

        rows = q.all()
        if not rows:
            return 0

        try:
            for row in rows:
                db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return len(rows)

    def list_calibrations(self, db: Session, *, instrument_id: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = (
            db.query(InstrumentCalibration)
            .filter(InstrumentCalibration.instrument_id == int(instrument_id))
            .order_by(InstrumentCalibration.ts.desc(), InstrumentCalibration.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [
            {
                "id": int(row.id),
                "instrument_id": int(row.instrument_id),
                "ts": row.ts,
                "next_due_at": row.next_due_at,
                "method": row.method,
                "result": row.result,
                "as_found": row.as_found,
                "as_left": row.as_left,
                "performed_by": row.performed_by,
                "certificate_no": row.certificate_no,
                "notes": row.notes,
                "meta": row.meta or {},
            }
            for row in rows
        ]

    def add_calibration(
        self,
        db: Session,
        *,
        instrument_id: int,
        ts: Optional[dt.datetime] = None,
        next_due_at: Optional[dt.datetime] = None,
        method: Optional[str] = None,
        result: Optional[str] = None,
        as_found: Optional[float] = None,
        as_left: Optional[float] = None,
        performed_by: Optional[str] = None,
        certificate_no: Optional[str] = None,
        notes: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if db.query(Instrument.id).filter(Instrument.id == int(instrument_id)).one_or_none() is None:
            raise ValueError("instrument not found")

        row = InstrumentCalibration(
            instrument_id=int(instrument_id),
            ts=(ts or _utcnow()),
            next_due_at=next_due_at,
            method=(str(method).strip() if method is not None else None),
            result=(str(result).strip() if result is not None else None),
            as_found=(float(as_found) if as_found is not None else None),
            as_left=(float(as_left) if as_left is not None else None),
            performed_by=(str(performed_by).strip() if performed_by is not None else None),
            certificate_no=(str(certificate_no).strip() if certificate_no is not None else None),
            notes=(str(notes).strip() if notes is not None else None),
            meta=(meta or {}),
        )

        try:
            db.add(row)
            db.commit()
            db.refresh(row)
        except Exception:
            db.rollback()
            raise

        return {
            "id": int(row.id),
            "instrument_id": int(row.instrument_id),
            "ts": row.ts,
            "next_due_at": row.next_due_at,
            "method": row.method,
            "result": row.result,
            "as_found": row.as_found,
            "as_left": row.as_left,
            "performed_by": row.performed_by,
            "certificate_no": row.certificate_no,
            "notes": row.notes,
            "meta": row.meta or {},
        }

    def add_attachment(
        self,
        db: Session,
        *,
        instrument_id: int,
        filename: str,
        storage_path: Optional[str] = None,
        content_type: Optional[str] = None,
        notes: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if db.query(Instrument.id).filter(Instrument.id == int(instrument_id)).one_or_none() is None:
            raise ValueError("instrument not found")

        filename_v = _safe_filename(filename)
        storage_path_v = str(storage_path).strip() if storage_path is not None else _default_attachment_storage_path(int(instrument_id), filename_v)

        row = InstrumentAttachment(
            instrument_id=int(instrument_id),
            filename=filename_v,
            storage_path=storage_path_v,
            content_type=(str(content_type).strip() if content_type is not None else None),
            notes=(str(notes).strip() if notes is not None else None),
            meta=(meta or {}),
        )

        try:
            db.add(row)
            db.commit()
            db.refresh(row)
        except Exception:
            db.rollback()
            raise

        return {
            "id": int(row.id),
            "instrument_id": int(row.instrument_id),
            "filename": row.filename,
            "storage_path": row.storage_path,
            "content_type": row.content_type,
            "uploaded_at": row.uploaded_at,
            "notes": row.notes,
            "meta": row.meta or {},
        }

    def list_attachments(self, db: Session, *, instrument_id: int) -> list[dict[str, Any]]:
        rows = (
            db.query(InstrumentAttachment)
            .filter(InstrumentAttachment.instrument_id == int(instrument_id))
            .order_by(InstrumentAttachment.uploaded_at.desc(), InstrumentAttachment.id.desc())
            .all()
        )
        return [
            {
                "id": int(row.id),
                "instrument_id": int(row.instrument_id),
                "filename": row.filename,
                "storage_path": row.storage_path,
                "content_type": row.content_type,
                "uploaded_at": row.uploaded_at,
                "notes": row.notes,
                "meta": row.meta or {},
            }
            for row in rows
        ]

    def remove_attachment(self, db: Session, *, attachment_id: int) -> bool:
        row = db.query(InstrumentAttachment).filter(InstrumentAttachment.id == int(attachment_id)).one_or_none()
        if row is None:
            return False
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True

    def map_spare(
        self,
        db: Session,
        *,
        instrument_id: int,
        part_id: int,
        qty_required: int = 1,
        notes: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if db.query(Instrument.id).filter(Instrument.id == int(instrument_id)).one_or_none() is None:
            raise ValueError("instrument not found")

        part = db.query(SparePart).filter(SparePart.id == int(part_id)).one_or_none()
        if part is None:
            raise ValueError("spare part not found")

        qty_v = int(qty_required)
        if qty_v < 1:
            raise ValueError("qty_required must be >= 1")

        row = (
            db.query(InstrumentSpareMap)
            .filter(
                InstrumentSpareMap.instrument_id == int(instrument_id),
                InstrumentSpareMap.part_id == int(part_id),
            )
            .one_or_none()
        )

        if row is None:
            row = InstrumentSpareMap(
                instrument_id=int(instrument_id),
                part_id=int(part_id),
                qty_required=qty_v,
                notes=(str(notes).strip() if notes is not None else None),
                meta=(meta or {}),
            )
        else:
            row.qty_required = qty_v
            row.notes = (str(notes).strip() if notes is not None else None)
            row.meta = (meta or {})

        try:
            db.add(row)
            db.commit()
            db.refresh(row)
        except Exception:
            db.rollback()
            raise

        return {
            "id": int(row.id),
            "instrument_id": int(row.instrument_id),
            "part_id": int(row.part_id),
            "qty_required": int(row.qty_required),
            "notes": row.notes,
            "meta": row.meta or {},
            "part": {
                "id": int(part.id),
                "part_code": str(part.part_code),
                "name": str(part.name),
                "unit": part.unit,
            },
        }

    def list_spare_map(self, db: Session, *, instrument_id: int) -> list[dict[str, Any]]:
        rows = (
            db.query(InstrumentSpareMap)
            .options(selectinload(InstrumentSpareMap.part))
            .filter(InstrumentSpareMap.instrument_id == int(instrument_id))
            .order_by(InstrumentSpareMap.id.asc())
            .all()
        )
        return [
            {
                "id": int(row.id),
                "instrument_id": int(row.instrument_id),
                "part_id": int(row.part_id),
                "qty_required": int(row.qty_required),
                "notes": row.notes,
                "meta": row.meta or {},
                "part": {
                    "id": int(row.part.id),
                    "part_code": str(row.part.part_code),
                    "name": str(row.part.name),
                    "unit": row.part.unit,
                }
                if row.part is not None
                else None,
            }
            for row in rows
        ]

    def unmap_spare(self, db: Session, *, instrument_id: int, part_id: int) -> bool:
        row = (
            db.query(InstrumentSpareMap)
            .filter(
                InstrumentSpareMap.instrument_id == int(instrument_id),
                InstrumentSpareMap.part_id == int(part_id),
            )
            .one_or_none()
        )
        if row is None:
            return False
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True
