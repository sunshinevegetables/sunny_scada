from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sunny_scada.api.deps import get_data_points_service
from sunny_scada.api.schemas import UpdateDataPointRequest
from sunny_scada.services.data_points_service import DataPointsService

router = APIRouter(tags=["data-points"])


@router.get("/get_data_point", summary="Get Data Point", description="Fetch a specific data point from the data_points.yaml file.")
def get_data_point(path: str, svc: DataPointsService = Depends(get_data_points_service)):
    data = svc.get_by_path(path)
    if data is None:
        raise HTTPException(status_code=404, detail="Data point not found.")
    return data


@router.post("/update_data_point", summary="Update Data Point", description="Update an existing data point in the YAML file.")
def update_data_point(req: UpdateDataPointRequest, svc: DataPointsService = Depends(get_data_points_service)):
    ok = svc.update_point_at_path(
        path=req.path,
        point_data={
            "type": req.type,
            "description": req.description,
            "address": req.address,
            **({"bits": req.bits} if req.type == "DIGITAL" and req.bits else {}),
        },
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Path not found or update failed.")
    return {"message": f"Data point '{req.name}' updated successfully at {req.path}."}


@router.post("/add_data_point", summary="Add Data Point", description="Add a data point into the YAML file dynamically.")
def add_data_point(req: UpdateDataPointRequest, svc: DataPointsService = Depends(get_data_points_service)):
    ok = svc.add_point(
        parent_path=req.path,
        name=req.name,
        point_data={
            "type": req.type,
            "description": req.description,
            "address": req.address,
            **({"bits": req.bits} if req.type == "DIGITAL" and req.bits else {}),
        },
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to add data point.")
    return {"message": f"Data point '{req.name}' added successfully to {req.path}."}
