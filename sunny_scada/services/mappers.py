from __future__ import annotations

from typing import Any, Dict


def map_temperature_points(storage_data: Dict[str, Any], process_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Builds a map for temperature points where:
      value['process'] == process_name and value['monitor'] == 1
    Key format preserved to match your existing alarm audio filenames:
      f"{data_type} {process} {description}"
    """
    out: Dict[str, Dict[str, Any]] = {}

    for plc_name, plc_blob in (storage_data or {}).items():
        data_section = (plc_blob or {}).get("data", {})
        if not isinstance(data_section, dict):
            continue

        for _section_name, section_data in data_section.items():
            if not isinstance(section_data, dict):
                continue

            for data_type, data_points in section_data.items():
                if not isinstance(data_points, dict):
                    continue

                for _point_name, point_details in data_points.items():
                    read_data = (point_details or {}).get("read", {})
                    if not isinstance(read_data, dict):
                        continue

                    for _key, value in read_data.items():
                        if not isinstance(value, dict):
                            continue
                        process = value.get("process")
                        monitor = value.get("monitor")
                        description = value.get("description")
                        if process != process_name or monitor != 1:
                            continue

                        full_name = f"{data_type} {process} {description}"
                        out[full_name] = {
                            "description": description,
                            "type": value.get("type"),
                            "raw_value": value.get("raw_value"),
                            "scaled_value": value.get("scaled_value"),
                        }
    return out


def map_monitored_data(storage_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map all points with monitor == 1 (any process)."""
    out: Dict[str, Dict[str, Any]] = {}

    for plc_name, plc_blob in (storage_data or {}).items():
        data_section = (plc_blob or {}).get("data", {})
        if not isinstance(data_section, dict):
            continue

        for _section_name, section_data in data_section.items():
            if not isinstance(section_data, dict):
                continue

            for data_type, data_points in section_data.items():
                if not isinstance(data_points, dict):
                    continue

                for _point_name, point_details in data_points.items():
                    read_data = (point_details or {}).get("read", {})
                    if not isinstance(read_data, dict):
                        continue

                    for _key, value in read_data.items():
                        if not isinstance(value, dict):
                            continue
                        monitor = value.get("monitor")
                        description = value.get("description")
                        process = value.get("process")
                        if monitor != 1:
                            continue

                        full_name = f"{data_type} {process} {description}"
                        out[full_name] = {
                            "description": description,
                            "type": value.get("type"),
                            "raw_value": value.get("raw_value"),
                            "scaled_value": value.get("scaled_value"),
                        }
    return out


def map_condensers_to_control_status(storage_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    for plc_name, plc_blob in (storage_data or {}).items():
        cond_section = (plc_blob or {}).get("data", {}).get("cond", {})
        if not isinstance(cond_section, dict):
            continue

        for cond_type, condensers in cond_section.items():
            if not isinstance(condensers, dict):
                continue
            for cond_name, cond_data in condensers.items():
                read_data = (cond_data or {}).get("read", {})
                if not isinstance(read_data, dict):
                    continue
                for key, value in read_data.items():
                    if not (isinstance(key, str) and key.startswith("EVAP_COND") and "CTRL_STS" in key):
                        continue
                    if not isinstance(value, dict):
                        continue

                    bit_9 = (value.get("value") or {}).get("BIT 9", {}).get("value")
                    desc = value.get("description")
                    if bit_9 is None or desc is None:
                        continue

                    full = f"{plc_name}_{cond_type}_{cond_name}_{key}"
                    out[full] = {"description": desc, "Pump On": bool(bit_9)}
    return out


def map_compressors_to_status(storage_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    for plc_name, plc_blob in (storage_data or {}).items():
        comp_section = (plc_blob or {}).get("data", {}).get("comp", {})
        if not isinstance(comp_section, dict):
            continue

        for comp_type, compressors in comp_section.items():
            if not isinstance(compressors, dict):
                continue
            for comp_name, comp_data in compressors.items():
                read_data = (comp_data or {}).get("read", {})
                if not isinstance(read_data, dict):
                    continue
                for key, value in read_data.items():
                    if key not in {"COMP_1_STATUS_2", "COMP_2_STATUS_2", "COMP_3_STATUS_2", "COMP_4_STATUS_2"}:
                        continue
                    bit_7 = (value.get("value") or {}).get("BIT 7", {}).get("value")
                    desc = value.get("description")
                    if bit_7 is None or desc is None:
                        continue
                    full = f"{plc_name}_{comp_type}_{comp_name}_{key}"
                    out[full] = {"description": desc, "Running": bool(bit_7)}
    return out
