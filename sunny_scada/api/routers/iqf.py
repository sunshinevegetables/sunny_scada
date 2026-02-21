from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sunny_scada.api.deps import get_iqf_service, require_permission
from sunny_scada.services.iqf_service import IQFService

router = APIRouter(tags=["iqf"])


@router.post("/start_iqf", summary="Start IQF Monitoring", description="Start IQF (sequence + checks).")
def start_iqf(
    svc: IQFService = Depends(get_iqf_service),
    _perm=Depends(require_permission("iqf:control")),
):
    try:
        svc.start_iqf()
        return {"message": "IQF started successfully."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop_iqf", summary="Stop IQF Monitoring", description="Stop IQF monitoring (placeholder).")
def stop_iqf(_perm=Depends(require_permission("iqf:control"))):
    # Your original code had a monitoring thread here, but it is currently not enabled.
    # Keep endpoint for compatibility.
    return {"message": "IQF stop requested (no background IQF monitor currently running)."}
