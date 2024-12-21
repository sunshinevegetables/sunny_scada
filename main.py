from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
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
import yaml
from playsound import playsound 
import pygame
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)



# Initialize shared components
storage = DataStorage()
plc_reader = PLCReader(storage=storage)

# Initialize shared PLC writer
screw_comp_writer = PLCWriter(config_type="screw_comp")
viltor_comp_writer = PLCWriter(config_type="viltor_comp") 
plc_writer = PLCWriter(config_type="plc")

# Define stop events for threads
stop_events = {
   
    "plc": threading.Event(),
    "alarms": threading.Event()
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

# Define the request model
class StartPackhouseRequest(BaseModel):
    plc_name: str

# Path to the data_points.yaml file
DATA_POINTS_FILE = "config/data_points.yaml"

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


# Custom lifespan manager using asynccontextmanager
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application...")
    threads = []
    # Enable specific threads
    
    threads.append(threading.Thread(target=update_plc_data, daemon=True))
    
   
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

# Function to start the packhouse process
@app.post("/start_packhouse", summary="Start Packhouse", description="Initiate the packhouse start-up process.")
def start_packhouse(request: StartPackhouseRequest):
    """
    API endpoint to start the packhouse.

    This function starts the required machines and monitors the process to ensure the packhouse is operational.

    :param request: Request body containing the PLC name.
    :return: Success or failure message.
    """
    plc_name = request.plc_name

    try:
        # Validate PLC name
        if plc_name not in screw_comp_writer.clients:
            raise HTTPException(status_code=400, detail=f"PLC '{plc_name}' not found in configuration.")

        # Step 1: Start the compressor
        logger.info(f"Starting compressor on PLC '{plc_name}'...")
        if not screw_comp_writer.write_signal(plc_name, "COMPRESSOR START", 1, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to start compressor.")

        # Add 10-second delay between commands
        logger.info("Waiting for 10 seconds before enabling load mode...")
        time.sleep(20)

        # Step 2: Enable load mode
        logger.info(f"Enabling load mode on PLC '{plc_name}'...")
        if not screw_comp_writer.write_signal(plc_name, "COMPRESSOR STOP", 1, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to enable load mode.")

        # Step 3: Monitor suction pressure
        logger.info("Monitoring suction pressure...")
        

        return {"message": f"Packhouse started successfully on '{plc_name}'. Suction pressure is stable."}

    except Exception as e:
        logger.error(f"Error in start_packhouse: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while starting the packhouse.")
    
@app.post("/start_iqf", summary="Start IQF Process", description="Initiate the IQF start-up process.")
def start_iqf():
    """
    API endpoint to start the IQF process.

    This function performs the steps to start and stop compressors in sequence with delays.

    :return: Success or failure message.
    """
    try:
        # Step 1: Start Compressor A
        logger.info("Starting compressor: 'Micrologix1400 Screw Comp-A'...")
        if not screw_comp_writer.write_signal("Micrologix1400 Screw Comp-A", "COMPRESSOR START", 1, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to start compressor 'Micrologix1400 Screw Comp-A'.")

        logger.info("Waiting for 10 seconds before next step...")
        time.sleep(10)

        # Step 2: Start Compressor B
        logger.info("Starting compressor: 'Micrologix1400 Screw Comp-B'...")
        if not screw_comp_writer.write_signal("Micrologix1400 Screw Comp-B", "COMPRESSOR START", 1, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to start compressor 'Micrologix1400 Screw Comp-B'.")

        logger.info("Waiting for 10 seconds before next step...")
        time.sleep(10)

        # Step 3: Start Compressor D
        logger.info("Starting compressor: 'Micrologix1400 Screw Comp-D'...")
        if not screw_comp_writer.write_signal("Micrologix1400 Screw Comp-D", "COMPRESSOR START", 1, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to start compressor 'Micrologix1400 Screw Comp-D'.")

        logger.info("Waiting for 10 seconds before next step...")
        time.sleep(10)

        # Step 4: Stop Compressor A
        logger.info("Stopping compressor: 'Micrologix1400 Screw Comp-A'...")
        if not screw_comp_writer.write_signal("Micrologix1400 Screw Comp-A", "COMPRESSOR STOP", 0, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to stop compressor 'Micrologix1400 Screw Comp-A'.")

        logger.info("Waiting for 10 seconds before next step...")
        time.sleep(10)

        # Step 5: Stop Compressor D
        logger.info("Stopping compressor: 'Micrologix1400 Screw Comp-D'...")
        if not screw_comp_writer.write_signal("Micrologix1400 Screw Comp-D", "COMPRESSOR STOP", 0, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to stop compressor 'Micrologix1400 Screw Comp-D'.")

        logger.info("Waiting for 10 seconds before next step...")
        time.sleep(10)

        # Step 6: Stop Compressor B
        logger.info("Stopping compressor: 'Micrologix1400 Screw Comp-B'...")
        if not screw_comp_writer.write_signal("Micrologix1400 Screw Comp-B", "COMPRESSOR STOP", 0, "config/screw_comp_write_points.yaml"):
            raise HTTPException(status_code=500, detail="Failed to stop compressor 'Micrologix1400 Screw Comp-B'.")

        return {"message": "IQF process completed successfully."}

    except Exception as e:
        logger.error(f"Error in start_iqf: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while starting the IQF process.")

def monitor_screw_comp_suction_pressure():
    """
    Monitors the suction pressure for all screw compressors defined in the configuration.

    - Uses a flag to ensure actions for starting/stopping the compressor and condenser are performed only once.
    """
    flags = {}  # A dictionary to maintain flags for each compressor

    while not stop_events["monitor_screw_suction_pressure"].is_set():
        try:
            logger.info("Starting suction pressure monitoring...")

            # Fetch data from storage
            storage_data = storage.get_data()
            logger.debug(f"Storage Data: {storage_data}")

            # Extract data for "Main PLC"
            main_plc_data = storage_data.get("Main PLC", {}).get("data", {})
            screw_comps = main_plc_data.get("comp", {}).get("screw", {})
            if not screw_comps:
                logger.warning("No screw compressors data found in storage. Skipping...")
                time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 10)))
                continue

            for comp_name, comp_data in screw_comps.items():
                logger.info(f"Processing suction pressure for {comp_name}...")

                # Safely access the suction pressure
                suction_pressure_data = (
                    comp_data.get("read", {}).get("COMP_1_SUC_PRESSURE", {})
                )
                suction_pressure = suction_pressure_data.get("scaled_value")

                if suction_pressure is None:
                    logger.warning(
                        f"Suction pressure data not available for {comp_name}. Skipping..."
                    )
                    continue

                logger.info(f"Suction pressure for {comp_name}: {suction_pressure}")

                # Initialize the flag for the compressor if not already set
                if comp_name not in flags:
                    flags[comp_name] = False  # False means the compressor is not running

                # Control logic based on suction pressure and the flag state
                if suction_pressure >= 50 and not flags[comp_name]:
                    logger.warning(
                        f"High suction pressure detected for {comp_name}: {suction_pressure} >= 50. Starting condenser, compressor, and loading compressor."
                    )
                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 42022, 0, 1
                    )  # Start condenser
                    time.sleep(0.1)  # Short delay for bit toggling
                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 42022, 0, 0
                    )  # Toggle off

                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 41336, 0, 1
                    )  # Start compressor
                    time.sleep(0.1)  # Short delay for bit toggling
                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 41336, 0, 0
                    )  # Toggle off

                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 41336, 3, 1
                    )  # Load compressor

                    flags[comp_name] = True  # Set the flag indicating the compressor is running

                elif suction_pressure <= 35 and flags[comp_name]:
                    logger.warning(
                        f"Low suction pressure detected for {comp_name}: {suction_pressure} <= 35. Unloading compressor, stopping compressor, and stopping condenser."
                    )
                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 41336, 4, 1
                    )  # Unload compressor

                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 41336, 1, 1
                    )  # Stop compressor
                    time.sleep(0.1)  # Short delay for bit toggling
                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 41336, 1, 0
                    )  # Toggle off

                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 42022, 0, 1
                    )  # Stop condenser
                    time.sleep(0.1)  # Short delay for bit toggling
                    screw_comp_writer.bit_write_signal(
                        "Main PLC", 42022, 0, 0
                    )  # Toggle off

                    flags[comp_name] = False  # Reset the flag indicating the compressor is stopped

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 10)))

        except Exception as e:
            logger.error(f"Error during suction pressure monitoring: {e}")




def monitor_viltor_comp_suction_pressure():
    """
    Monitors the suction pressure for all Viltor compressors defined in the configuration.

    - Reads the configuration file to get compressor details.
    - Fetches real-time data from the shared storage.
    - Monitors the suction pressure and triggers an alarm if it exceeds 35.
    """
    SUCTION_PRESSURE_THRESHOLD = 35  # Threshold for suction pressure
    POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL_PLC", 60))  # Polling interval in seconds

    while not stop_events["monitor_viltor_suction_pressure"].is_set():
        try:
            logger.info("Starting Viltor compressor suction pressure monitoring...")

            # Load the Viltor compressor configuration
            config_file = "config/config.yaml"
            viltor_comps = plc_reader.load_config(config_file).get("viltor_comp", [])

            if not viltor_comps:
                logger.error(f"No Viltor compressors found in configuration file: {config_file}")
                return

            for viltor_comp in viltor_comps:
                plc_name = viltor_comp.get("name")
                if not plc_name:
                    logger.warning("Skipping a Viltor compressor with no name in the configuration.")
                    continue

                # Fetch data from storage
                data = storage.get_data().get(plc_name, {})
                logger.debug(f"Fetched data for {plc_name}: {data}")

                if not data:
                    logger.warning(f"No data available for {plc_name}. Skipping...")
                    continue

                # Access nested data for SUCTION PRESSURE
                suction_pressure = (
                    data.get("data", {})
                    .get("read", {})
                    .get("VILTER_1_SUC_PRESSURE", {})
                    .get("scaled_value")
                )

                if suction_pressure is None:
                    logger.warning(f"Suction pressure data not found for {plc_name}. Skipping...")
                    continue

                logger.info(f"Suction Pressure for {plc_name}: {suction_pressure}")

                # Check if suction pressure exceeds the threshold
                if suction_pressure > SUCTION_PRESSURE_THRESHOLD:
                    logger.warning(
                        f"WARNING: Suction pressure for {plc_name} is above threshold: {suction_pressure} > {SUCTION_PRESSURE_THRESHOLD}"
                    )
                    play_alarm()  # Trigger the alarm
                else:
                    logger.info(f"Suction pressure for {plc_name} is normal: {suction_pressure}")

            # Wait for the next polling interval
            time.sleep(POLLING_INTERVAL)

        except Exception as e:
            logger.error(f"Error during Viltor compressor suction pressure monitoring: {e}")


def play_alarm():
    pygame.mixer.init()
    pygame.mixer.music.load("static/high_suction_pressure_alarm.wav")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():  # Wait for the music to finish
        continue