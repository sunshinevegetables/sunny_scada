from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import AuditLog


class AuditService:
    """Simple audit log helper.

    Cycle 1 focuses on config changes and auth events.
    """

    def log(
        self,
        db: Session,
        *,
        action: str,
        user_id: Optional[int],
        client_ip: Optional[str],
        resource: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        config_revision_id: Optional[int] = None,
    ) -> AuditLog:
        entry = AuditLog(
            action=action,
            user_id=user_id,
            client_ip=client_ip,
            resource=resource,
            meta=metadata or {},
            config_revision_id=config_revision_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
