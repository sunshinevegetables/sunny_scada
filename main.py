from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from gtts import gTTS
from sunny_scada import plc_reader
from sunny_scada.plc_reader import PLCReader
from sunny_scada.plc_writer import PLCWriter
from sunny_scada.data_storage import DataStorage
from config_loader import load_config
from pydantic import BaseModel
import threading
import logging
import os
import time
import pygame
import yaml
from playsound import playsound 
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse

import queue

# Queue to store alarm details
alarm_queue = queue.Queue()

# Thread to process the alarm queue
alarm_processor_thread = None

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize shared components
storage = DataStorage()
plc_reader = PLCReader(storage=storage)

# Initialize shared PLC writer
plc_writer = PLCWriter()

# Define processes
processes = ["IQF", "AllRound Packhouse", "Cold Storage", "Frozen"]

# Define stop events for threads
stop_events = {
   
    "plc": threading.Event(),
    "suction_pressure_map": threading.Event(),
    "data_monitor_map": threading.Event(),
    "condenser_control_map": threading.Event(),
    "frozen_storage_map": threading.Event(),
    "cold_storage_map": threading.Event(),
    }

# Define the model for the request body
class WriteSignalRequest(BaseModel):
    plc_type: str  
    plc_name: str
    signal_name: str
    value: int

class BitWriteSignalRequest(BaseModel):
    plc_type: str         # e.g., "screw_comp", "viltor_comp", or "plc"
    plc_name: str         # Name of the PLC
    register: str         # Register name (e.g., "COMP_1_STATUS_1")
    bit: int              # Bit position to write (0-15)
    value: int            # Value to write (0 or 1)

class BitReadSignalRequest(BaseModel):
    plc_name: str
    plc_type: str
    register: str
    bit: int

# Path to the data_points.yaml file
DATA_POINTS_FILE = "config/data_points.yaml"

# Global variable for monitoring thread
monitoring_thread = None

# Define the model for the request body
class UpdateDataPointRequest(BaseModel):
    path: str  # Hierarchical path in the YAML file, e.g., "plcs/comp/viltor/comp_1/read"
    name: str  # Name of the new data point
    type: str  # Type of the data point (e.g., REAL, DIGITAL)
    description: str  # Description of the data point
    address: int  # Modbus address
    bits: dict = None  # Only applicable for DIGITAL type data points

# Helper function to update the nested dictionary
def update_nested_dict(data: dict, path: list, key: str, value: dict):
    if not path:
        data[key] = value
        return
    current_key = path.pop(0)
    if current_key not in data:
        data[current_key] = {}
    update_nested_dict(data[current_key], path, key, value)

# Background thread for reading PLC data
def update_plc_data():
    while not stop_events["plc"].is_set():
        try:
            logger.info("Starting PLC data read cycle...")
            
            # Read data from PLCs
            all_plc_data = plc_reader.read_plcs_from_config(
                config_file="config/config.yaml",
                data_points_file="config/data_points.yaml"
            )   

            # Log and handle the aggregated data if necessary
            if all_plc_data:
                logger.debug(f"Aggregated PLC data: {all_plc_data}")
            else:
                logger.warning("No data received during PLC read cycle.")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 1)))
        except KeyboardInterrupt:
            logger.info("Shutting down application...")
            plc_reader.close_clients()
        except FileNotFoundError as e:
            logger.error(f"Configuration or points file not found: {e}")
            break  # Break the loop if a critical file is missing
        except Exception as e:
            logger.error(f"Unexpected error in PLC thread: {e}")

suction_pressure_map = {}  # Global dictionary to store suction pressure data
condenser_control_map = {}  # Global dictionary to store condenser control status data
data_monitor_map = {}  # Global dictionary to store monitored data points
def update_suction_pressure_map(interval=1):
    """
    Update the suction pressure map at regular intervals.
    If any suction pressure exceeds 45, print an alarm message.
    """
    global suction_pressure_map
    while not stop_events["suction_pressure_map"].is_set():
        try:
            # Fetch updated data from storage
            if not storage.get_data():
                logger.warning("No data available in storage. Retrying...")
                time.sleep(interval)
                continue

            logger.info("Updating suction pressure map...")
            suction_pressure_map = map_comps_to_suction_pressure()
            logger.info(f"Suction Pressure Map: {suction_pressure_map}")

            # Check suction pressure values
            for comp_name, suction_pressure in suction_pressure_map.items():
                if suction_pressure > 50:
                    logger.warning(f"ALARM: Suction pressure for {comp_name} exceeds threshold: {suction_pressure}")
                    trigger_alarm(comp_name, suction_pressure)

        except Exception as e:
            logger.error(f"Error updating suction pressure map: {e}")

        time.sleep(interval)


# Custom lifespan manager using asynccontextmanager
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application...")
    threads = []
    # Enable specific threads
    
    threads.append(threading.Thread(target=update_plc_data, daemon=True))
    #threads.append(threading.Thread(target=update_suction_pressure_map, args=(5,), daemon=True))
    #threads.append(threading.Thread(target=update_data_monitor_map, args=(10,), daemon=True))
    threads.append(threading.Thread(target=update_frozen_storage_map, args=(10,), daemon=True))
    threads.append(threading.Thread(target=update_cold_storage_map, args=(10,), daemon=True))
    
    #threads.append(threading.Thread(target=update_condenser_control_map, daemon=True))
    # Start all enabled threads
    for thread in threads:
        thread.start()

    try:
        yield
    finally:
        logger.info("Shutting down application...")
        # Signal all threads to stop
        for event in stop_events.values():
            event.set()
        # Wait for all threads to join
        for thread in threads:
            if thread.is_alive():
                thread.join()

# Create FastAPI app with custom lifespan
app = FastAPI(lifespan=lifespan)

# Helper function to load processes from processes.yaml
def load_processes():
    config_path = "config/processes.yaml"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as file:
                data = yaml.safe_load(file)
                return data.get("processes", [])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error reading YAML file: {str(e)}")
    else:
        raise HTTPException(status_code=404, detail="Processes configuration file not found.")

# Endpoint to get all configured processes
@app.get("/processes", summary="Get Configured Processes", description="Fetch the list of all configured processes.")
async def get_processes():
    processes = load_processes()
    if not processes:
        raise HTTPException(status_code=404, detail="No processes configured.")
    return {"processes": processes}

@app.get("/get_data_point", summary="Get Data Point", description="Fetch a specific data point from the data_points.yaml file.")
def get_data_point(path: str):
    try:
        with open(DATA_POINTS_FILE, "r") as file:
            data_points = yaml.safe_load(file)

        # Navigate through the nested structure using the path
        keys = path.split("/")
        data = data_points
        for key in keys:
            data = data.get(key, {})

        if not data:
            raise HTTPException(status_code=404, detail="Data point not found.")

        return data

    except Exception as e:
        logger.error(f"Error fetching data point: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while fetching the data point.")

@app.post("/update_data_point", summary="Update Data Point", description="Update an existing data point in the YAML file.")
def update_data_point(request: UpdateDataPointRequest):
    """
    API to update an existing data point in the data_points.yaml file.

    :param request: JSON request containing the updated data point details.
    :return: Success message if the update is successful.
    """
    try:
        # Load the existing data_points.yaml
        if not os.path.exists(DATA_POINTS_FILE):
            raise FileNotFoundError(f"Data points file not found at {DATA_POINTS_FILE}")

        with open(DATA_POINTS_FILE, "r") as file:
            data_points = yaml.safe_load(file)

        if data_points is None:
            data_points = {}

        # Parse the hierarchical path into a list
        path_parts = request.path.split("/")
        name = request.name

        # Navigate to the parent of the data point to update
        parent = data_points
        for part in path_parts[:-1]:
            if part not in parent:
                raise HTTPException(status_code=404, detail=f"Path '{'/'.join(path_parts[:-1])}' not found in the data points.")
            parent = parent[part]

        # Check if the data point exists
        if path_parts[-1] not in parent:
            raise HTTPException(status_code=404, detail=f"Data point '{path_parts[-1]}' not found at the specified path.")

        # Update the data point with new details
        updated_data_point = {
            "type": request.type,
            "description": request.description,
            "address": request.address,
        }

        # Add bits if the type is DIGITAL
        if request.type == "DIGITAL" and request.bits:
            updated_data_point["bits"] = request.bits

        # Update the data point in the parent
        parent[path_parts[-1]] = updated_data_point

        # Save the updated data points back to the YAML file
        with open(DATA_POINTS_FILE, "w") as file:
            yaml.dump(data_points, file, default_flow_style=False)

        logger.info(f"Data point '{name}' successfully updated at {request.path}.")
        return {"message": f"Data point '{name}' updated successfully at {request.path}."}

    except FileNotFoundError as e:
        logger.error(e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating data point: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")


@app.post("/add_data_point", summary="Add Data Point", description="Add the data_points.yaml file dynamically.")
def update_data_point(request: UpdateDataPointRequest):
    """
    API to dynamically add the data_points.yaml file.

    :param request: JSON request containing the new data point details.
    :return: Success message if the add is successful.
    """
    try:
        # Load the existing data_points.yaml
        if not os.path.exists(DATA_POINTS_FILE):
            raise FileNotFoundError(f"Data points file not found at {DATA_POINTS_FILE}")

        with open(DATA_POINTS_FILE, "r") as file:
            data_points = yaml.safe_load(file)

        if data_points is None:
            data_points = {}

        # Parse the path into a list
        path_parts = request.path.split("/")
        name = request.name

        # Create the new data point entry
        new_data_point = {
            "type": request.type,
            "description": request.description,
            "address": request.address,
        }

        # Include bits if the type is DIGITAL
        if request.type == "DIGITAL" and request.bits:
            new_data_point["bits"] = request.bits

        # Update the nested dictionary
        update_nested_dict(data_points, path_parts, name, new_data_point)

        # Save the updated data points back to the YAML file
        with open(DATA_POINTS_FILE, "w") as file:
            yaml.dump(data_points, file, default_flow_style=False)

        logger.info(f"Data point '{name}' successfully added to {request.path}.")
        return {"message": f"Data point '{name}' added successfully to {request.path}."}

    except FileNotFoundError as e:
        logger.error(e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating data point: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")

# Mount the static files
app.mount("/static", StaticFiles(directory="static", html=True), name="static")
app.mount("/frontend", StaticFiles(directory="static", html=True), name="frontend")
# Serve the config directory
app.mount("/config", StaticFiles(directory="config"), name="config")

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust the origin list as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def map_comps_to_suction_pressure():
    """
    Maps compressor names to their suction pressure values.

    :return: A dictionary where keys are compressor names and values are suction pressures.
    """
    try:
        # Fetch data from storage
        data = storage.get_data()

        if not data:
            logger.warning("No data available in storage.")
            return {}

        # Initialize dictionary to store compressor names and suction pressures
        suction_pressure_map = {}

        # Traverse the data structure
        for plc_name, plc_data in data.items():
            comp_section = plc_data.get("data", {}).get("comp", {})
            for comp_type, compressors in comp_section.items():
                for comp_name, comp_data in compressors.items():
                    read_data = comp_data.get("read", {})
                    for key, value in read_data.items():
                        # Check if the description matches 'Suction Pressure'
                        if value.get("description", "").lower() == "suction pressure":
                            suction_pressure = value.get("scaled_value")
                            # Add to the map if suction pressure is found
                            if suction_pressure is not None:
                                full_comp_name = f"{plc_name}_{comp_type}_{comp_name}"
                                suction_pressure_map[full_comp_name] = suction_pressure

        if not suction_pressure_map:
            logger.warning("No suction pressure data found for compressors.")
            return {}

        logger.debug("Successfully created suction pressure map.")
        return suction_pressure_map

    except Exception as e:
        logger.error(f"Error creating suction pressure map: {e}")
        return {}

def update_condenser_control_map(interval=1):
    """
    Update the condenser control status map at regular intervals.
    """
    global condenser_control_map
    while not stop_events["condenser_control_map"].is_set():
        try:
            # Fetch and update the condenser control map
            logger.debug("Updating condenser control map...")
            condenser_control_map = map_condensers_to_control_status()
            logger.debug(f"Condenser Control Map: {condenser_control_map}")

        except Exception as e:
            logger.error(f"Error updating condenser control map: {e}")

        time.sleep(interval)

def map_condensers_to_control_status():
    """
    Maps condenser names to their control status data, including address and BIT 9 values.

    :return: A dictionary where keys are condenser names and values are their control status data.
    """
    try:
        # Fetch data from storage
        data = storage.get_data()

        if not data:
            logger.warning("No data available in storage.")
            return {}

        # Initialize dictionary to store condenser names and control status data
        condenser_control_map = {}

        # Traverse the data structure
        for plc_name, plc_data in data.items():
            condenser_section = plc_data.get("data", {}).get("cond", {})
            for cond_type, condensers in condenser_section.items():
                for cond_name, cond_data in condensers.items():
                    read_data = cond_data.get("read", {})
                    for key, value in read_data.items():
                        # Match keys with condenser control status entries
                        if key.startswith("EVAP_COND") and "CTRL_STS" in key:
                            bit_9_value = value.get("value", {}).get("BIT 9", {}).get("value")
                            description = value.get("description")

                            if bit_9_value is not None and description is not None:
                                full_cond_name = f"{plc_name}_{cond_type}_{cond_name}_{key}"
                                condenser_control_map[full_cond_name] = {
                                    "description": description,
                                    "Pump On": bit_9_value,
                                }

        if not condenser_control_map:
            logger.warning("No condenser control status data found.")
            return {}

        logger.debug("Successfully created condenser control status map.")
        return condenser_control_map

    except Exception as e:
        logger.error(f"Error creating condenser control status map: {e}")
        return {}

def map_compressors_to_status():
    """
    Maps compressor names to their status data, including address and BIT 7 values.

    :return: A dictionary where keys are compressor names and values are their status data.
    """
    try:
        # Fetch data from storage
        data = storage.get_data()

        if not data:
            logger.warning("No data available in storage.")
            return {}

        # Initialize dictionary to store compressor names and status data
        compressor_status_map = {}

        # Traverse the data structure
        for plc_name, plc_data in data.items():
            compressor_section = plc_data.get("data", {}).get("comp", {})  # Assuming "comp" holds compressors
            for comp_type, compressors in compressor_section.items():
                for comp_name, comp_data in compressors.items():
                    read_data = comp_data.get("read", {})
                    for key, value in read_data.items():
                        # Match keys with compressor status entries
                        if key in ["COMP_1_STATUS_2", "COMP_2_STATUS_2", "COMP_3_STATUS_2", "COMP_4_STATUS_2"]:
                            bit_7_value = value.get("value", {}).get("BIT 7", {}).get("value")  # Extract bit 7 (ON/OFF)
                            description = value.get("description")

                            if bit_7_value is not None and description is not None:
                                full_comp_name = f"{plc_name}_{comp_type}_{comp_name}_{key}"
                                compressor_status_map[full_comp_name] = {
                                    "description": description,
                                    "Running": bit_7_value,
                                }

        if not compressor_status_map:
            logger.warning("No compressor status data found.")
            return {}

        logger.debug("Successfully created compressor status map.")
        return compressor_status_map

    except Exception as e:
        logger.error(f"Error creating compressor status map: {e}")
        return {}

def map_frozen_storage_temps():
    """
    Creates a map for all temperature data points in frozen storage rooms where monitoring is enabled.

    :return: A dictionary where keys are chamber names and values are temperature details.
    """
    try:
        # Fetch data from storage
        data = storage.get_data()

        if not data:
            logger.warning("No data available in storage.")
            return {}

        frozen_storage_map = {}

        # Traverse the data structure
        for plc_name, plc_data in data.items():
            data_section = plc_data.get("data", {})
            for section_name, section_data in data_section.items():
                for data_type, data_points in section_data.items():
                    for point_name, point_details in data_points.items():
                        read_data = point_details.get("read", {})
                        for key, value in read_data.items():
                            #logger.info(f"Value: {value}")
                            process = value.get("process")
                            description = value.get("description")
                            monitor = value.get("monitor")
                            #logger.info(f"Process: {process}, Description: {description}, Monitor: {monitor}")
                            max_audio = value.get("max_audio")
                            min_audio = value.get("min_audio")
                            if process == "FROZEN":
                                if monitor == 1:
                                    #logger.info(f"Process: {process}, Description: {description}, Monitor: {monitor}")
                                    full_point_name = f"{data_type} {process} {description}"
                                    frozen_storage_map[full_point_name] = {
                                        "description": description,
                                        "type": value.get("type"),
                                        "raw_value": value.get("raw_value"),
                                        "scaled_value": value.get("scaled_value"),
                                        "higher_register": value.get("higher_register"),
                                        "low_register": value.get("low_register"),
                                        "monitor": monitor,
                                        "process": process,
                                        "max": value.get("max"),
                                        "min": value.get("min"),
                                        "max_audio": max_audio,  # Include max_audio
                                        "min_audio": min_audio  # Include min_audio
                                    }

        if not frozen_storage_map:
            logger.warning("No frozen storage temperature data found.")
            return {}

        logger.info("Successfully created frozen storage temperature map.")
        return frozen_storage_map

    except Exception as e:
        logger.error(f"Error creating frozen storage temperature map: {e}")
        return {}

def update_frozen_storage_map(interval=1):
    """
    Regularly checks the global frozen storage temperature map for breaches of min and max values and raises alarms.

    :param interval: Interval in seconds for checking the temperature map.
    """
    global frozen_storage_map
    while not stop_events["frozen_storage_map"].is_set():
        try:
            logger.info("Checking frozen storage temperature map for breaches...")

            # Refresh the monitored temperature data map
            frozen_storage_map = map_frozen_storage_temps()
            #logger.info(f"Frozen Storage Map: {frozen_storage_map}")
            if not frozen_storage_map:
                logger.warning("No frozen storage data points available. Retrying...")
                time.sleep(interval)
                continue

            for point_name, value in frozen_storage_map.items():
                scaled_value = value.get("scaled_value")
                max_value = value.get("max")
                min_value = value.get("min")

                if scaled_value is not None:
                    if max_value is not None and scaled_value > max_value:
                        logger.warning(f"ALARM: {point_name} exceeds max value: {scaled_value} > {max_value}")
                        trigger_alarm(point_name, scaled_value, "max")
                    if min_value is not None and scaled_value < min_value:
                        logger.warning(f"ALARM: {point_name} below min value: {scaled_value} < {min_value}")
                        trigger_alarm(point_name, scaled_value, "min")

            # Sleep in smaller increments to allow responsive stopping
            for _ in range(interval * 10):  
                if stop_events["frozen_storage_map"].is_set():
                    logger.info("Stop event detected. Exiting monitoring loop.")
                    return
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Error in frozen storage temperature monitoring: {e}")

def map_cold_storage_temps():
    """
    Creates a map for all temperature data points in cold storage rooms where monitoring is enabled.

    :return: A dictionary where keys are chamber names and values are temperature details.
    """
    try:
        # Fetch data from storage
        data = storage.get_data()

        if not data:
            logger.warning("No data available in storage.")
            return {}

        cold_storage_map = {}

        # Traverse the data structure
        for plc_name, plc_data in data.items():
            data_section = plc_data.get("data", {})
            for section_name, section_data in data_section.items():
                for data_type, data_points in section_data.items():
                    for point_name, point_details in data_points.items():
                        read_data = point_details.get("read", {})
                        for key, value in read_data.items():
                            process = value.get("process")
                            description = value.get("description")
                            monitor = value.get("monitor")
                            max_audio = value.get("max_audio")
                            min_audio = value.get("min_audio")

                            # Process only Cold Storage Data
                            if process == "COLD" and monitor == 1:
                                full_point_name = f"{data_type} {process} {description}"
                                cold_storage_map[full_point_name] = {
                                    "description": description,
                                    "type": value.get("type"),
                                    "raw_value": value.get("raw_value"),
                                    "scaled_value": value.get("scaled_value"),
                                    "higher_register": value.get("higher_register"),
                                    "low_register": value.get("low_register"),
                                    "monitor": monitor,
                                    "process": process,
                                    "max": value.get("max"),
                                    "min": value.get("min"),
                                    "max_audio": max_audio,  # Include max_audio
                                    "min_audio": min_audio  # Include min_audio
                                }

        if not cold_storage_map:
            logger.warning("No cold storage temperature data found.")
            return {}

        logger.info("Successfully created cold storage temperature map.")
        return cold_storage_map

    except Exception as e:
        logger.error(f"Error creating cold storage temperature map: {e}")
        return {}

def update_cold_storage_map(interval=1):
    """
    Regularly checks the global cold storage temperature map for breaches of min and max values and raises alarms.

    :param interval: Interval in seconds for checking the temperature map.
    """
    global cold_storage_map
    while not stop_events["cold_storage_map"].is_set():
        try:
            logger.info("Checking cold storage temperature map for breaches...")

            # Refresh the monitored temperature data map
            cold_storage_map = map_cold_storage_temps()
            
            if not cold_storage_map:
                logger.warning("No cold storage data points available. Retrying...")
                time.sleep(interval)
                continue

            for point_name, value in cold_storage_map.items():
                scaled_value = value.get("scaled_value")
                max_value = value.get("max")
                min_value = value.get("min")

                if scaled_value is not None:
                    if max_value is not None and scaled_value > max_value:
                        logger.warning(f"ALARM: {point_name} exceeds max value: {scaled_value} > {max_value}")
                        trigger_alarm(point_name, scaled_value, "max")
                    if min_value is not None and scaled_value < min_value:
                        logger.warning(f"ALARM: {point_name} below min value: {scaled_value} < {min_value}")
                        trigger_alarm(point_name, scaled_value, "min")

            # Sleep in smaller increments to allow responsive stopping
            for _ in range(interval * 10):  
                if stop_events["cold_storage_map"].is_set():
                    logger.info("Stop event detected. Exiting monitoring loop.")
                    return
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Error in cold storage temperature monitoring: {e}")

def map_monitored_data():
    """
    Creates a map for all data points where the monitor value is 1.

    :return: A dictionary where keys are data point names and values are their details.
    """
    try:
        # Fetch data from storage
        data = storage.get_data()

        if not data:
            logger.warning("No data available in storage.")
            return {}
        #logger.info(f"Data: {data}")
        monitored_data_map = {}

        # Traverse the data structure
        for plc_name, plc_data in data.items():
            data_section = plc_data.get("data", {})
            for section_name, section_data in data_section.items():
                for data_type, data_points in section_data.items():
                    for point_name, point_details in data_points.items():
                        read_data = point_details.get("read", {})
                        for key, value in read_data.items():
                            #logger.info(f"Value: {value}")
                            process = value.get("process")
                            description = value.get("description")
                            monitor = value.get("monitor")
                            #logger.info(f"Process: {process}, Description: {description}, Monitor: {monitor}")
                            max_audio = value.get("max_audio")
                            min_audio = value.get("min_audio")
                            if monitor == 1:
                                logger.info(f"Process: {process}, Description: {description}, Monitor: {monitor}")
                                full_point_name = f"{process} {description}"
                                monitored_data_map[full_point_name] = {
                                    "description": description,
                                    "type": value.get("type"),
                                    "raw_value": value.get("raw_value"),
                                    "scaled_value": value.get("scaled_value"),
                                    "higher_register": value.get("higher_register"),
                                    "low_register": value.get("low_register"),
                                    "monitor": monitor,
                                    "process": process,
                                    "max": value.get("max"),
                                    "min": value.get("min"),
                                    "max_audio": max_audio,  # Include max_audio
                                    "min_audio": min_audio  # Include min_audio
                                }

        if not monitored_data_map:
            logger.warning("No monitored data points found.")
            return {}

        logger.info("Successfully created monitored data map.")
        #logger.info(f"Map: {monitored_data_map}")
        return monitored_data_map

    except Exception as e:
        logger.error(f"Error creating monitored data map: {e}")
        return {}


def update_data_monitor_map(interval=1):
    """
    Regularly checks the global data_monitor_map for breaches of min and max values and raises alarms.

    :param interval: Interval in seconds for checking the data monitor map.
    """
    global data_monitor_map
    while not stop_events["data_monitor_map"].is_set():
        try:
            logger.info("Checking data monitor map for value breaches...")

            # Refresh the monitored data map
            data_monitor_map = map_monitored_data()

            if not data_monitor_map:
                logger.warning("No monitored data points available. Retrying...")
                time.sleep(interval)
                continue

            for point_name, value in data_monitor_map.items():
                scaled_value = value.get("scaled_value")
                max_value = value.get("max")
                min_value = value.get("min")

                if scaled_value is not None:
                    if max_value is not None and scaled_value > max_value:
                        logger.warning(f"ALARM: {point_name} exceeds max value: {scaled_value} > {max_value}")
                        trigger_alarm(point_name, scaled_value, "max")
                    if min_value is not None and scaled_value < min_value:
                        logger.warning(f"ALARM: {point_name} below min value: {scaled_value} < {min_value}")
                        trigger_alarm(point_name, scaled_value, "min")
            
            # Sleep in smaller increments to allow responsive stopping
            for _ in range(interval * 10):  # Divide the sleep into smaller chunks
                if stop_events["data_monitor_map"].is_set():
                    logger.info("Stop event detected. Exiting monitoring loop.")
                    return
                time.sleep(0.1)
        
        except Exception as e:
            logger.error(f"Error in data monitor map check: {e}")



@app.post("/start_iqf", summary="Start IQF Monitoring", description="Start monitoring PLC data for threshold breaches.")
def start_iqf():
    global monitoring_thread
      
    # Fetch condenser control map
    condenser_control_map = map_condensers_to_control_status()
    compressor_status_map = map_compressors_to_status()

    if not condenser_control_map:
        raise HTTPException(status_code=400, detail="No condenser control status data available.")
    logger.info(f"Condenser Control Map: {condenser_control_map}")
    # Check if any condenser is on before starting the IQF process
    condenser_on = any(details["Pump On"] for details in condenser_control_map.values())
    if not condenser_on:
        logger.info("No condenser is currently on. Attempting to turn on condenser 1.")

        # Perform the first write operation (set bit 0 to 1)
        success_first = plc_writer.bit_write_signal(plc_writer.clients.get("Main PLC"), 42022, 0, 1)
        if not success_first:
            logger.error("Failed to set bit 0 to 1 for condenser 1.")
            raise HTTPException(status_code=500, detail="Failed to turn on condenser 1 (first operation).")

        logger.info("Successfully set bit 0 to 1 for condenser 1. Now resetting bit 0 to 0.")

        # Perform the second write operation (reset bit 0 to 0)
        success_second = plc_writer.bit_write_signal(plc_writer.clients.get("Main PLC"), 42022, 0, 0)
        if not success_second:
            logger.error("Failed to reset bit 0 to 0 for condenser 1.")
            raise HTTPException(status_code=500, detail="Failed to reset condenser 1 (second operation).")

        logger.info("Condenser 1 turned on and reset successfully. Verifying status...")

        time.sleep(10)
        # Check if condenser pump and fans are on
        client = plc_reader.clients.get("Main PLC")
        if not client:
            logger.error("No Modbus client found for PLC 'Main PLC'.")
            raise HTTPException(status_code=500, detail="Failed to verify condenser status (no client available).")

        bit_status = plc_reader.read_data_point(
            client,
            point_name="Condenser 1 Pump Status",
            point_details={
                "address": 42022,
                "type": "DIGITAL",
                "description": "Pump On Status",
                "bits": {"BIT 9": "PUMP ON"}
            },
        )

        if not bit_status or not bit_status.get("value", {}).get("BIT 9", {}).get("value", False):
            logger.error("Condenser 1 failed to turn on. Bit 9 is not True.")
            raise HTTPException(status_code=500, detail="Condenser 1 failed to turn on (verification failed).")

        logger.info("Condenser 1 is now ON. Verification successful.")

    else:
        logger.info("At least one condenser is already ON. Proceeding with IQF start.")
           
    # start montoiring thread for condenser pump and fans

    # Check if screw compressor 2 is on
    
   
    # Wait before checking the compressor status
    time.sleep(10)

    if not compressor_status_map:
        raise HTTPException(status_code=400, detail="No compressor status data available.")
    logger.info(f"Compressor Status Map: {compressor_status_map}")

    # Check if Compressor 1 is ON
    comp1_key = "MainPLC_screw_COMP_1_STATUS_2"
    is_comp1_on = compressor_status_map.get(comp1_key, {}).get("Running", 0)

    if is_comp1_on:
        logger.info("Compressor 1 is already ON.")
    else:
        logger.info("Compressor 1 is OFF. Attempting to turn it on.")

        # Get the Modbus client for the PLC
        client = plc_reader.clients.get("Main PLC")
        if not client:
            logger.error("No Modbus client found for PLC 'Main PLC'.")
            raise HTTPException(status_code=500, detail="Failed to verify Compressor 1 status (no client available).")

        try:
            # Perform the first write operation: set bit 0 to 1
            success_first = plc_writer.bit_write_signal(client, 41340, 0, 1)
            if not success_first:
                logger.error("Failed to set bit 0 to 1 for Compressor 1.")
                raise HTTPException(status_code=500, detail="Failed to turn on Compressor 1 (first operation).")

            logger.info("Successfully set bit 0 to 1 for Compressor 1. Now resetting bit 0 to 0.")

            # Perform the second write operation: reset bit 0 to 0
            success_second = plc_writer.bit_write_signal(client, 41340, 0, 0)
            if not success_second:
                logger.error("Failed to reset bit 0 to 0 for Compressor 1.")
                raise HTTPException(status_code=500, detail="Failed to reset Compressor 1 (second operation).")

            logger.info("Compressor 1 turned on and reset successfully.")

        except Exception as e:
            logger.error(f"Error while processing Compressor 1 operations: {e}")
            raise HTTPException(status_code=500, detail="An error occurred while managing Compressor 1.")



    # Wait before checking the compressor status
    time.sleep(10)

    # Fetch compressor control map
    compressor_status_map = map_compressors_to_status()
    if not compressor_status_map:
        raise HTTPException(status_code=400, detail="No compressor status data available.")
    logger.info(f"Compressor Status Map: {compressor_status_map}")

    # Check if Compressor 4 is ON
    comp4_key = "MainPLC_screw_COMP_4_STATUS_2"  # Adjust key format if necessary
    is_comp4_on = compressor_status_map.get(comp4_key, {}).get("Running", 0)

    if is_comp4_on:
        logger.info("Screw Compressor 4 is already ON.")
    else:
        logger.info("Screw Compressor 4 is OFF. Attempting to turn it on.")

        # Get the Modbus client for the PLC
        client = plc_reader.clients.get("Main PLC")
        if not client:
            logger.error("No Modbus client found for PLC 'Main PLC'.")
            raise HTTPException(status_code=500, detail="Failed to verify Compressor 4 status (no client available).")

        try:
            # Perform the first write operation: set bit 0 to 1
            success_first = plc_writer.bit_write_signal(client, 41348, 0, 1)  # 41348 = Start command for COMP 4
            if not success_first:
                logger.error("Failed to set bit 0 to 1 for Screw Compressor 4.")
                raise HTTPException(status_code=500, detail="Failed to turn on Screw Compressor 4 (first operation).")

            logger.info("Successfully set bit 0 to 1 for Screw Compressor 4. Now resetting bit 0 to 0.")

            # Perform the second write operation: reset bit 0 to 0
            success_second = plc_writer.bit_write_signal(client, 41348, 0, 0)
            if not success_second:
                logger.error("Failed to reset bit 0 to 0 for Screw Compressor 4.")
                raise HTTPException(status_code=500, detail="Failed to reset Screw Compressor 4 (second operation).")

            logger.info("Screw Compressor 4 turned on and reset successfully.")

        except Exception as e:
            logger.error(f"Error while processing Screw Compressor 4 operations: {e}")
            raise HTTPException(status_code=500, detail="An error occurred while managing Screw Compressor 4.")

    # check if screw compressor 2 suction pressure is below 30 psi

    # start monitoring thread for screw compressor 2 suction pressure

    # Check if liquid pump is on


    # Start monitoring thread on compresser 4 suction pressure
    """
    if monitoring_thread and monitoring_thread.is_alive():
        raise HTTPException(status_code=400, detail="IQF monitoring is already running.")

    logger.info("Starting IQF monitoring thread...")
    stop_events["data_monitor_map"].clear()  # Clear the stop event to allow monitoring
    monitoring_thread = threading.Thread(target=update_data_monitor_map, args=(5,), daemon=True)
    monitoring_thread.start()
    """
    return {"message": "IQF started successfully."}


@app.post("/stop_iqf", summary="Stop IQF Monitoring", description="Stop monitoring PLC data.")
def stop_iqf():
    global monitoring_thread

    if not monitoring_thread or not monitoring_thread.is_alive():
        raise HTTPException(status_code=400, detail="IQF monitoring is not running.")

    logger.info("Stopping IQF monitoring thread...")
    stop_events["data_monitor_map"].set()  # Signal the thread to stop
    monitoring_thread.join(timeout=5)  # Wait for the thread to terminate
    if monitoring_thread.is_alive():
        logger.error("Failed to stop IQF monitoring thread within the timeout.")
        raise HTTPException(status_code=500, detail="Failed to stop IQF monitoring thread.")
    
    monitoring_thread = None  # Reset the thread reference
    logger.info("IQF monitoring stopped successfully.")
    return {"message": "IQF monitoring stopped successfully."}

@app.get("/plc_data", summary="Get PLC Data", description="Fetch the latest data from all configured PLCs.")
def get_plc_data():
    return storage.get_data()

@app.post("/bit_read_signal", summary="Read a Bit Signal from PLC", description="Read a specific bit from a Modbus register.")
def bit_read_signal(request: BitReadSignalRequest):
    """
    API endpoint to read a specific bit in a Modbus register.
    """
    logger.info(f"Received bit read signal request: {request}")
    try:
        # Load the read points from the unified data_points.yaml file
        read_points_path = "config/data_points.yaml"

        # Load the read points
        with open(read_points_path, "r") as file:
            data_points = yaml.safe_load(file).get("data_points", {}).get("plcs", {})
        
        # Navigate to the appropriate register within the YAML structure
        target_register = None
        for plc_category, plc_data in data_points.items():
            #logger.info(f"PLC Category: {plc_category}")
            for sub_category, sub_data in plc_data.items():
                #logger.info(f"Sub Category: {sub_category}")
                for sub_sub_category, sub_sub_data in sub_data.items():
                    #logger.info(f"Sub Sub Category: {sub_sub_category}")
                    read_data = sub_sub_data.get("read", {})
                    #logger.info(f"Read Data: {read_data}")
                
                    if request.register in read_data:
                        target_register = read_data[request.register]
                        #logger.info(f"Target Register: {target_register}")
                        break
                if target_register:
                    break
            if target_register:
                break

        if not target_register:
            raise HTTPException(status_code=400, detail=f"Register '{request.register}' not recognized in {read_points_path}.")

        # Extract the address and bit details
        register_address = target_register.get("address")
        bits = target_register.get("bits", {})

        if register_address is None or f"BIT {request.bit}" not in bits:
            raise HTTPException(status_code=400, detail=f"Invalid bit '{request.bit}' for register '{request.register}'.")

        # Get the Modbus client for the PLC
        client = plc_reader.clients.get(request.plc_name)
        if not client:
            logger.error(f"No Modbus client found for PLC '{request.plc_name}'.")
            raise HTTPException(status_code=400, detail=f"PLC '{request.plc_name}' not found.")

        # Read the signal using the PLCReader's `read_single_bit` method
        bit_value = plc_reader.read_single_bit(client, register_address, request.bit)

        if bit_value is None:
            raise HTTPException(status_code=500, detail="Failed to read bit signal.")

        return {
            "message": f"Successfully read value {bit_value} from bit {request.bit} of register {request.register} on {request.plc_name}",
            "value": bit_value
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in bit_read_signal endpoint: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred.")


@app.post("/bit_write_signal", summary="Write a Bit Signal to PLC", description="Send a bitwise signal to a specific PLC.")
def bit_write_signal(request: BitWriteSignalRequest):
    """
    API endpoint to write a specific bit in a Modbus register.
    """
    logger.info(f"Received bit write signal request: {request}")
    try:
        
        
        # Navigate to the appropriate register within the YAML structure
        target_register = None
        #logger.info(f"PLC Writer: {plc_writer.write_signals}")
        for plcs, data_points in plc_writer.write_signals.items():
            for plc_category, plc_data in data_points.items():
                #logger.info(f"PLC Category: {plc_category}")
                for sub_category, sub_data in plc_data.items():
                    #logger.info(f"Sub Category: {sub_category}")
                    for sub_sub_category, sub_sub_data in sub_data.items():
                        #logger.info(f"Sub Sub Category: {sub_sub_category}")
                        write_data = sub_sub_data.get("write", {})
                        #logger.info(f"Write Data: {write_data}")
                    
                        if request.register in write_data:
                            target_register = write_data[request.register]
                            logger.info(f"Target Register: {target_register}")
                            break
                    if target_register:
                        break
                if target_register:
                    break

        if not target_register:
            raise HTTPException(status_code=400, detail=f"Register '{request.register}' not recognized in write points.")

        
        # Extract the register address and bit mapping
        register_address = target_register.get("address")
        bits = target_register.get("bits", {})

        if register_address is None or f"BIT {request.bit}" not in bits:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid bit '{request.bit}' for register '{request.register}'. Available bits: {list(bits.keys())}",
            )

        # Get the Modbus client for the PLC
        client = plc_writer.clients.get(request.plc_name)
        if not client:
            logger.error(f"No Modbus client found for PLC '{request.plc_name}'.")
            raise HTTPException(status_code=400, detail=f"PLC '{request.plc_name}' not found.")
        
        # Write the signal using the PLCWriter
        success = plc_writer.bit_write_signal(client, register_address, request.bit, request.value)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to write bit signal.")

        return {"message": f"Successfully wrote value {request.value} to bit {request.bit} of register {request.register} on {request.plc_name}"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in bit_write_signal endpoint: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred.")

alarm_thread = None
alarm_thread_lock = threading.Lock()
alarm_processor_thread = None
active_alarms = set()

def process_alarm_queue():
    """Continuously processes the alarm queue and plays alarms."""
    while not alarm_queue.empty():
        try:
            point_name, value, threshold_type = alarm_queue.get()
            alarm_worker(point_name, value, threshold_type)
        except Exception as e:
            logger.error(f"Error processing alarm queue: {e}")
        finally:
            active_alarms.discard(point_name)
            logger.info(f"Finished processing alarm for {point_name}.")


def alarm_worker(point_name, value, threshold_type):
    """
    Creates an audio file, plays it, and deletes it after playback.
    :param point_name: Name of the alarm point
    :param value: The threshold-breached value
    :param threshold_type: 'max' or 'min' threshold breach
    """
    try:
        rounded_value = round(value)
        alarm_message = f"Alarm triggered for {point_name}. Value {rounded_value} has breached the {threshold_type} threshold."

        # Generate alarm audio file
        audio_file = f"static/sounds/{point_name}_{threshold_type}.mp3"
        tts = gTTS(text=alarm_message, lang="en")
        tts.save(audio_file)

        logger.warning(alarm_message)
        logger.info(f"Generated alarm audio: {audio_file}")

        # Initialize pygame mixer
        pygame.mixer.init()
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        logger.info(f"Playing alarm audio: {audio_file}")

        # Wait for audio playback or timeout
        start_time = time.time()
        while pygame.mixer.music.get_busy():
            if time.time() - start_time > 10:
                logger.warning(f"Audio playback for {point_name} timed out after 10 seconds.")
                pygame.mixer.music.stop()
                break
            pygame.time.Clock().tick(10)

        pygame.mixer.music.stop()
        logger.info(f"Stopping alarm audio: {audio_file}")

        # Delete the generated audio file
        if os.path.exists(audio_file):
            os.remove(audio_file)
            logger.info(f"Deleted alarm audio file: {audio_file}")

    except Exception as e:
        logger.error(f"Error in alarm worker: {e}")

    finally:
        pygame.mixer.quit()


def trigger_alarm(point_name, value, threshold_type):
    """
    Triggers an alarm for a monitored point.
    :param point_name: Name of the alarm point
    :param value: The threshold-breached value
    :param threshold_type: 'max' or 'min' threshold breach
    """
    if point_name in active_alarms:
        logger.info(f"Alarm for {point_name} is already active. Skipping re-trigger.")
        return

    # Add the alarm to the queue
    alarm_queue.put((point_name, value, threshold_type))
    active_alarms.add(point_name)
    logger.info(f"Alarm for {point_name} added to the queue.")

    # Start the alarm processor thread if not already running
    global alarm_processor_thread
    if not alarm_processor_thread or not alarm_processor_thread.is_alive():
        alarm_processor_thread = threading.Thread(target=process_alarm_queue, daemon=True)
        alarm_processor_thread.start()
        logger.info("Started alarm processor thread.")


