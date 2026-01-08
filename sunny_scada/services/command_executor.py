from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import Command, CommandEvent
from sunny_scada.plc_writer import PLCWriter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkItem:
    command_row_id: int


class CommandExecutor:
    """Executes queued PLC write commands asynchronously.

    - Per-PLC queues to serialize writes.
    - Non-blocking HTTP: API stores a command row and enqueues it.

    Safety: Command creation must already validate the datapoint against
    `data_points.yaml` and store resolved write parameters in payload.
    """

    def __init__(
        self,
        *,
        sessionmaker: Callable[[], Session],
        writer: PLCWriter,
        max_retries: int = 2,
        backoff_s: float = 0.25,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._writer = writer
        self._max_retries = max(0, int(max_retries))
        self._backoff_s = max(0.0, float(backoff_s))

        self._stop = threading.Event()
        self._started = False

        self._queues: Dict[str, "queue.Queue[WorkItem]"] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        # workers are started lazily per PLC as commands arrive
        self._started = True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            threads = list(self._threads.values())
        for t in threads:
            t.join(timeout=3)

    def enqueue(self, plc_name: str, command_row_id: int) -> None:
        if not self._started:
            self.start()
        plc_name = str(plc_name)
        with self._lock:
            q = self._queues.get(plc_name)
            if q is None:
                q = queue.Queue()
                self._queues[plc_name] = q
                t = threading.Thread(target=self._worker, args=(plc_name,), daemon=True, name=f"cmd-{plc_name}")
                self._threads[plc_name] = t
                t.start()
        q.put(WorkItem(command_row_id=command_row_id))

    def _add_event(self, db: Session, cmd: Command, status: str, message: Optional[str] = None, meta: Optional[dict] = None) -> None:
        db.add(
            CommandEvent(
                command_row_id=cmd.id,
                status=status,
                message=message,
                meta=meta or {},
            )
        )

    def _worker(self, plc_name: str) -> None:
        q = self._queues[plc_name]
        while not self._stop.is_set():
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                with self._sessionmaker() as db:
                    cmd = db.query(Command).filter(Command.id == item.command_row_id).one_or_none()
                    if not cmd:
                        continue
                    if cmd.status != "queued":
                        continue

                    cmd.status = "executing"
                    cmd.attempts = int(cmd.attempts or 0)
                    self._add_event(db, cmd, "executing")
                    db.add(cmd)
                    db.commit()

                    ok = False
                    last_err: Optional[str] = None
                    for attempt in range(self._max_retries + 1):
                        if self._stop.is_set():
                            break
                        # reload to observe cancellation
                        db.refresh(cmd)
                        if cmd.status == "cancelled":
                            self._add_event(db, cmd, "cancelled")
                            db.commit()
                            ok = False
                            last_err = "cancelled"
                            break

                        cmd.attempts = int(cmd.attempts or 0) + 1
                        db.add(cmd)
                        db.commit()

                        try:
                            ok = self._execute(cmd)
                            if ok:
                                break
                            last_err = "write failed"
                        except Exception as e:
                            last_err = str(e)
                            ok = False

                        if attempt < self._max_retries:
                            time.sleep(self._backoff_s * (attempt + 1))

                    if ok:
                        cmd.status = "success"
                        cmd.error_message = None
                        self._add_event(db, cmd, "success")
                    else:
                        if cmd.status != "cancelled":
                            cmd.status = "failed"
                        cmd.error_message = last_err
                        self._add_event(db, cmd, cmd.status, message=last_err)

                    db.add(cmd)
                    db.commit()

            except Exception as e:
                logger.exception("Command worker error for plc=%s: %s", plc_name, e)
            finally:
                q.task_done()

    def _execute(self, cmd: Command) -> bool:
        payload = cmd.payload or {}
        kind = (cmd.kind or "").lower()
        plc_name = cmd.plc_name

        address_4x = payload.get("address")
        if address_4x is None:
            raise RuntimeError("Missing address")

        if kind == "bit":
            bit = payload.get("bit")
            value = payload.get("value")
            if bit is None or value is None:
                raise RuntimeError("Missing bit/value")
            return self._writer.bit_write_signal(plc_name, int(address_4x), int(bit), int(value), verify=True)

        if kind == "register":
            value = payload.get("value")
            if value is None:
                raise RuntimeError("Missing value")
            verify = bool(payload.get("verify", False))
            return self._writer.write_register(plc_name, int(address_4x), int(value), verify=verify)

        raise RuntimeError(f"Unsupported command kind: {cmd.kind}")
