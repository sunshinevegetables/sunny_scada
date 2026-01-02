from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sunny_scada.api.schemas import BitReadSignalRequest, BitWriteSignalRequest
from sunny_scada.api.deps import get_storage, get_reader, get_writer, get_data_points_service
from sunny_scada.services.data_points_service import DataPointsService

router = APIRouter(tags=["plc"])


@router.get("/plc_data", summary="Get PLC Data", description="Fetch the latest data from all configured PLCs.")
def get_plc_data(storage=Depends(get_storage)):
    return storage.get_data()


@router.post("/bit_read_signal", summary="Read a Bit Signal from PLC", description="Read a specific bit from a Modbus register.")
def bit_read_signal(
    req: BitReadSignalRequest,
    reader=Depends(get_reader),
    dp: DataPointsService = Depends(get_data_points_service),
):
    # Find register in YAML (read section)
    target = dp.find_register(req.register, direction="read")
    if not target:
        raise HTTPException(status_code=400, detail=f"Register '{req.register}' not recognized in read points.")

    register_address = target.get("address")
    bits = target.get("bits", {}) or {}
    if register_address is None or f"BIT {req.bit}" not in bits:
        raise HTTPException(status_code=400, detail=f"Invalid bit '{req.bit}' for register '{req.register}'.")

    bit_value = reader.read_single_bit(req.plc_name, int(register_address), int(req.bit))
    if bit_value is None:
        raise HTTPException(status_code=500, detail="Failed to read bit signal.")

    return {
        "message": f"Successfully read value {bit_value} from bit {req.bit} of register {req.register} on {req.plc_name}",
        "value": bit_value,
    }


@router.post("/bit_write_signal", summary="Write a Bit Signal to PLC", description="Send a bitwise signal to a specific PLC.")
def bit_write_signal(
    req: BitWriteSignalRequest,
    writer=Depends(get_writer),
    dp: DataPointsService = Depends(get_data_points_service),
):
    # Find register in YAML (write section)
    target = dp.find_register(req.register, direction="write")
    if not target:
        raise HTTPException(status_code=400, detail=f"Register '{req.register}' not recognized in write points.")

    register_address = target.get("address")
    bits = target.get("bits", {}) or {}
    if register_address is None or f"BIT {req.bit}" not in bits:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid bit '{req.bit}' for register '{req.register}'. Available bits: {list(bits.keys())}",
        )

    ok = writer.bit_write_signal(req.plc_name, int(register_address), int(req.bit), int(req.value), verify=True)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write bit signal.")

    return {"message": f"Successfully wrote value {req.value} to bit {req.bit} of register {req.register} on {req.plc_name}"}
