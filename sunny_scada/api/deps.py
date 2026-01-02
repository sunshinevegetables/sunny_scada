from __future__ import annotations

from fastapi import Request

from sunny_scada.core.settings import Settings
from sunny_scada.data_storage import DataStorage
from sunny_scada.plc_reader import PLCReader
from sunny_scada.plc_writer import PLCWriter
from sunny_scada.modbus_service import ModbusService
from sunny_scada.services.data_points_service import DataPointsService
from sunny_scada.services.alarm_service import AlarmService
from sunny_scada.services.iqf_service import IQFService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> DataStorage:
    return request.app.state.storage


def get_modbus(request: Request) -> ModbusService:
    return request.app.state.modbus


def get_reader(request: Request) -> PLCReader:
    return request.app.state.plc_reader


def get_writer(request: Request) -> PLCWriter:
    return request.app.state.plc_writer


def get_data_points_service(request: Request) -> DataPointsService:
    return request.app.state.data_points_service


def get_alarm_service(request: Request) -> AlarmService:
    return request.app.state.alarm_service


def get_iqf_service(request: Request) -> IQFService:
    return request.app.state.iqf_service
