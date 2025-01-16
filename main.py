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
plc_writer = PLCWriter(config_type="plc")

# Define processes
processes = ["IQF", "AllRound Packhouse", "Cold Storage", "Frozen Storage"]

# Define stop events for threads
stop_events = {
   
    "plc": threading.Event(),
    "suction_pressure_map": threading.Event(),
    "data_monitor_map": threading.Event(),
    "condenser_control_map": threading.Event()
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
    #threads.append(threading.Thread(target=update_data_monitor_map, args=(5,), daemon=True))
    threads.append(threading.Thread(target=update_condenser_control_map, daemon=True))
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

        logger.info("Successfully created suction pressure map.")
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
            logger.info("Updating condenser control map...")
            condenser_control_map = map_condensers_to_control_status()
            logger.info(f"Condenser Control Map: {condenser_control_map}")

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
            cond_section = plc_data.get("plc", {}).get("cond", {}).get("evap", {})
            for evap_cond_name, evap_cond_data in cond_section.items():
                control_status_data = evap_cond_data.get("EVAP_COND_*_CTRL_STS", {})
                address = control_status_data.get("address")
                bit_9_value = control_status_data.get("bits", {}).get("bit 9")

                if address is not None and bit_9_value is not None:
                    full_cond_name = f"{plc_name}_cond_evap_{evap_cond_name}"
                    condenser_control_map[full_cond_name] = {
                        "address": address,
                        "BIT 9": bit_9_value,
                    }

        if not condenser_control_map:
            logger.warning("No condenser control status data found.")
            return {}

        logger.info("Successfully created condenser control status map.")
        return condenser_control_map

    except Exception as e:
        logger.error(f"Error creating condenser control status map: {e}")
        return {}



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

        monitored_data_map = {}

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
                            if monitor == 1:
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
        logger.info(f"Map: {monitored_data_map}")
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
    # get data from storage
    
    data = storage.get_data()
    if not data:
        raise HTTPException(status_code=400, detail="No data available in storage. Please check the connection to the PLCs.")
    

    # Check if any condenser is on before starting the iqf process
    
    # start montoiring thread for condenser pump and fans

    # Check if screw compressor 2 is on
    
    

    # Check if screw compressor 4 is on

    # check if screw compressor 2 suction pressure is below 30 psi

    # start monitoring thread for screw compressor 2 suction pressure

    # Check if liquid pump is on


    # Start monitoring thread on compresser 4 suction pressure

    if monitoring_thread and monitoring_thread.is_alive():
        raise HTTPException(status_code=400, detail="IQF monitoring is already running.")

    logger.info("Starting IQF monitoring thread...")
    stop_events["data_monitor_map"].clear()  # Clear the stop event to allow monitoring

    monitoring_thread = threading.Thread(target=update_data_monitor_map, args=(5,), daemon=True)
    monitoring_thread.start()

    return {"message": "IQF monitoring started successfully."}


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

@app.post("/bit_write_signal", summary="Write a Bit Signal to PLC", description="Send a bitwise signal to a specific PLC.")
def bit_write_signal(request: BitWriteSignalRequest):
    """
    API endpoint to write a specific bit in a Modbus register.
    """
    logger.info(f"Received bit write signal request: {request}")
    try:
        # Determine the write points YAML file based on plc_type
        if request.plc_type == "screw_comp":
            write_points_path = "config/screw_comp_write_points.yaml"
        elif request.plc_type == "viltor_comp":
            write_points_path = "config/viltor_comp_write_points.yaml"
        elif request.plc_type == "plc":
            write_points_path = "config/plc_write_points.yaml"
        else:
            raise HTTPException(status_code=400, detail=f"Invalid PLC type: {request.plc_type}")

        # Load the write points
        with open(write_points_path, "r") as file:
            write_signals = yaml.safe_load(file).get("data_points", {})

        # Validate the register and bit
        if request.register not in write_signals:
            raise HTTPException(status_code=400, detail=f"Register '{request.register}' not recognized in {write_points_path}.")

        register_info = write_signals[request.register]
        register_address = register_info.get("register")
        bits = {bit["bit_name"]: bit["bit"] for bit in register_info.get("bits", [])}

        if register_address is None or request.bit not in bits.values():
            raise HTTPException(status_code=400, detail=f"Invalid bit '{request.bit}' for register '{request.register}'.")

        # Write the signal using the PLCWriter
        success = plc_writer.bit_write_signal(request.plc_name, register_address, request.bit, request.value)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to write bit signal.")

        return {"message": f"Successfully wrote value {request.value} to bit {request.bit} of register {request.register} on {request.plc_name}"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in bit_write_signal endpoint: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred.")

@app.post("/write_signal", summary="Write a Signal to PLC", description="Send a signal to a specific PLC.")
def write_signal(request: WriteSignalRequest):
    """
    API endpoint to write a signal to a PLC.
    """
    logger.info(f"Received write signal request: {request}")
    try:
        # Determine the write points YAML file based on plc_type
        if request.plc_type == "screw_comp":
            write_points_path = "config/screw_comp_write_points.yaml"
        elif request.plc_type == "viltor_comp":
            write_points_path = "config/viltor_comp_write_points.yaml"
        elif request.plc_type =="plc":
            write_points_path = "config/data_points.yaml"
        else:
            raise HTTPException(status_code=400, detail=f"Invalid PLC type: {request.plc_type}")

        # Load the write points
        with open(write_points_path, "r") as file:
            write_signals = yaml.safe_load(file).get("data_points", {})

        # Validate the signal name
        if request.signal_name not in write_signals:
            raise HTTPException(status_code=400, detail=f"Signal '{request.signal_name}' not recognized in {write_points_path}.")

        # Write the signal using the PLCWriter
        if request.plc_type == "screw_comp":
            logger.info(f"Screw Request: {request}")
            success = screw_comp_writer.write_signal(request.plc_name, request.signal_name, request.value)
        elif request.plc_type == "viltor_comp":
            logger.info(f"Viltor Request: {request}")
            success = viltor_comp_writer.viltor_write_signal(request.plc_name, request.signal_name, request.value)
        elif request.plc_type == "plc":
            success = plc_writer.plc_write_signal(request.plc_name, request.signal_name, request.value)
        else:
            raise HTTPException(status_code=400, detail=f"Invalid PLC type: {request.plc_type}")
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to write signal.")
        
        return {"message": f"Successfully wrote {request.value} to {request.signal_name} on {request.plc_name}"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in write_signal endpoint: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred.")


def trigger_alarm(comp_name, suction_pressure):
    logger.warning(f"ALARM TRIGGERED: {comp_name} - Suction Pressure: {suction_pressure}")
    # Play sound or send a notification
    pygame.mixer.init()
    pygame.mixer.music.load("static/sounds/high_suction_pressure_alarm.wav")
    pygame.mixer.music.play()

# A global variable to track the alarm thread
alarm_thread = None

# Lock to ensure only one alarm thread runs at a time
alarm_thread_lock = threading.Lock()

# A global set to track active alarms and avoid re-triggering for the same point
active_alarms = set()

def process_alarm_queue():
    while not alarm_queue.empty():
        try:
            # Fetch the next alarm from the queue
            point_name, value, threshold_type = alarm_queue.get()
            alarm_worker(point_name, value, threshold_type)

        except Exception as e:
            logger.error(f"Error processing alarm queue: {e}")

        finally:
            # Mark the alarm as processed
            active_alarms.discard(point_name)
            logger.info(f"Finished processing alarm for {point_name}.")

def alarm_worker(point_name, value, threshold_type):
    try:
        rounded_value = round(value)

        # Fetch audio file name from the data point configuration
        data_point = data_monitor_map.get(point_name, {})
        audio_file_key = f"{threshold_type}_audio"
        audio_file_name = data_point.get(audio_file_key)

        if not audio_file_name:
            logger.warning(f"No audio file specified for {point_name} ({threshold_type} breach). Skipping alarm.")
            return

        audio_file = f"static/sounds/{audio_file_name}.mp3"

        if not os.path.exists(audio_file):
            logger.error(f"Audio file not found: {audio_file}. Cannot play alarm.")
            return

        logger.warning(f"Alarm triggered for {point_name}. Value {rounded_value} has breached the {threshold_type} threshold.")
        
        # Initialize pygame mixer
        pygame.mixer.init()
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        logger.info(f"Playing alarm audio: {audio_file}")

        # Wait for audio playback or timeout
        start_time = time.time()
        while pygame.mixer.music.get_busy():
            if time.time() - start_time > 10:
                logger.warning(f"Audio playback for {point_name} timed out after 5 seconds.")
                pygame.mixer.music.stop()
                break
            pygame.time.Clock().tick(10)

        pygame.mixer.music.stop()
        logger.info(f"Stopping alarm audio: {audio_file}")

    except Exception as e:
        logger.error(f"Error in alarm worker: {e}")

    finally:
        pygame.mixer.quit()


def trigger_alarm(point_name, value, threshold_type):
    # Check if the alarm is already active
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


