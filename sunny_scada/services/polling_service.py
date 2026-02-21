from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sunny_scada.plc_reader import PLCReader

logger = logging.getLogger(__name__)


class PollingService:
    """Background PLC polling loop (safe start/stop)."""

    def __init__(
        self,
        plc_reader: PLCReader,
        interval_s: float,
        enable: bool = True,
        *,
        alarm_monitor=None,
        db_sessionmaker=None,
    ) -> None:
        self._reader = plc_reader
        self._interval_s = max(0.1, float(interval_s))
        self._enable = enable
        self._alarm_monitor = alarm_monitor
        self._db_sessionmaker = db_sessionmaker

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        logger.debug("PollingService.start() enter enable=%s interval=%s", self._enable, self._interval_s)
        if not self._enable:
            logger.info("PollingService disabled (ENABLE_PLC_POLLING=0).")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="plc-poller", daemon=True)
        self._thread.start()
        logger.info("PollingService started.")
        logger.debug("PollingService.start() called enable=%s", self._enable)


    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("PollingService stopped.")

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self._stop.is_set():
                return
            time.sleep(0.1)

    def _run(self) -> None:
        logger.debug("PollingService._run() thread started")
        logger.info("PollingService polling loop started.")

        while not self._stop.is_set():
            try:
                logger.debug("Polling tick")

                from sunny_scada.db.models import CfgDataPoint, CfgPLC, CfgContainer, CfgEquipment
                from sqlalchemy.orm import Session
                
                if self._db_sessionmaker is None:
                    logger.warning("PollingService: No db_sessionmaker provided")
                    self._sleep_interruptible(self._interval_s)
                    continue
                
                session: Optional[Session] = self._db_sessionmaker()

                db_data = []
                db_points = session.query(CfgDataPoint).all()
                
                # Map owner_id -> PLC name for efficient lookup
                plc_name_cache = {}
                
                def get_plc_name_for_datapoint(dp: CfgDataPoint, db: Session) -> Optional[str]:
                    """Resolve the actual PLC name for a data point."""
                    cache_key = (dp.owner_type, dp.owner_id)
                    if cache_key in plc_name_cache:
                        return plc_name_cache[cache_key]
                    
                    plc_name = None
                    if dp.owner_type == "plc":
                        # Direct PLC reference
                        plc = db.query(CfgPLC).filter(CfgPLC.id == dp.owner_id).first()
                        if plc:
                            plc_name = plc.name
                    elif dp.owner_type == "container":
                        # Container -> PLC
                        container = db.query(CfgContainer).filter(CfgContainer.id == dp.owner_id).first()
                        if container:
                            plc = db.query(CfgPLC).filter(CfgPLC.id == container.plc_id).first()
                            if plc:
                                plc_name = plc.name
                    elif dp.owner_type == "equipment":
                        # Equipment -> Container -> PLC
                        equipment = db.query(CfgEquipment).filter(CfgEquipment.id == dp.owner_id).first()
                        if equipment:
                            container = db.query(CfgContainer).filter(CfgContainer.id == equipment.container_id).first()
                            if container:
                                plc = db.query(CfgPLC).filter(CfgPLC.id == container.plc_id).first()
                                if plc:
                                    plc_name = plc.name
                    
                    plc_name_cache[cache_key] = plc_name
                    return plc_name
                
                # Group by PLC name for efficient batch polling
                points_by_plc = {}
                for dp in db_points:
                    if dp.address and dp.label:
                        plc_name = get_plc_name_for_datapoint(dp, session)
                        if plc_name:
                            points_by_plc.setdefault(plc_name, []).append(dp)

                for plc_name, points in points_by_plc.items():
                    # Batch poll all addresses for this PLC
                    batch_results = {}
                    storage_results = {}  # Separate format for DataStorage
                    for dp in points:
                        point_details = {
                            "address": dp.address,
                            "type": dp.type,
                            "description": dp.description,
                            "label": dp.label,
                        }
                        result = self._reader.read_data_point(plc_name, dp.label, point_details)
                        if result is not None:
                            # Use the same structure as DB
                            batch_results[dp.label] = {
                                "id": dp.id,
                                "owner_type": dp.owner_type,
                                "owner_id": dp.owner_id,
                                "label": dp.label,
                                "description": dp.description,
                                "category": dp.category,
                                "type": dp.type,
                                "address": dp.address,
                                "group_id": dp.group_id,
                                "class_id": dp.class_id,
                                "unit_id": dp.unit_id,
                                "multiplier": dp.multiplier,
                                "value": result.get("value"),
                                "raw_value": result.get("raw_value"),
                                "scaled_value": result.get("scaled_value"),
                                "timestamp": result.get("timestamp"),
                            }
                            # Store in format compatible with plc_data endpoint
                            storage_results[dp.label] = result
                    db_data.append({"plc_name": plc_name, "data_points": batch_results})
                    
                    # Store the polled data in DataStorage for plc_data endpoint
                    if self._reader.storage:
                        self._reader.storage.update_data(plc_name, storage_results)
                session.close()

                logger.debug("Polled DB datapoints data_type=%s", type(db_data))
                logger.debug("Polling snapshot sample=%s", str(db_data)[:800])

                if self._alarm_monitor and db_data:
                    logger.debug("Polling calling AlarmMonitor.process_plc_snapshot")
                    self._alarm_monitor.process_plc_snapshot(db_data)
                    logger.debug("AlarmMonitor.process_plc_snapshot returned")
                else:
                    logger.debug(
                        "AlarmMonitor not called monitor=%s data=%s",
                        bool(self._alarm_monitor),
                        bool(db_data),
                    )

            except Exception as e:
                logger.exception("PollingService error: %s", repr(e))

            self._sleep_interruptible(self._interval_s)

