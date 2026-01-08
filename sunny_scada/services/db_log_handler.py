from __future__ import annotations

import logging
from typing import Callable, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import ServerLog


class DBLogHandler(logging.Handler):
    """Writes selected log records to the DB.

    To avoid recursive logging loops, this handler should be attached only
    to a dedicated logger or use a level threshold.
    """

    def __init__(
        self,
        sessionmaker: Callable[[], Session],
        *,
        level: int = logging.WARNING,
    ) -> None:
        super().__init__(level=level)
        self._sessionmaker = sessionmaker

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._sessionmaker() as db:
                db.add(
                    ServerLog(
                        level=record.levelname,
                        logger=getattr(record, "name", None),
                        message=msg,
                        source="backend",
                        user_id=None,
                        client_ip=None,
                        meta={"pathname": record.pathname, "lineno": record.lineno},
                    )
                )
                db.commit()
        except Exception:
            # Never raise from logging
            return
