from __future__ import annotations

import os
import yaml
from fastapi import APIRouter, Depends, HTTPException

from sunny_scada.api.deps import get_settings
from sunny_scada.core.settings import Settings

router = APIRouter(tags=["processes"])


@router.get("/processes", summary="Get Configured Processes", description="Fetch the list of all configured processes.")
def get_processes(settings: Settings = Depends(get_settings)):
    path = settings.processes_file
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Processes configuration file not found.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        processes = data.get("processes", []) or []
        if not processes:
            raise HTTPException(status_code=404, detail="No processes configured.")
        return {"processes": processes}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading processes file: {e}")
