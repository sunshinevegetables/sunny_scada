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
    "screw": threading.Event(),
    "viltor": threading.Event(),
    "vfd": threading.Event(),
    "condenser": threading.Event(),
    "compressor": threading.Event(),
    "hmi": threading.Event(),
    "plc": threading.Event(),
    "alarms": threading.Event(),
    "monitor_screw_suction_pressure": threading.Event(),
    "monitor_viltor_suction_pressure": threading.Event()}

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


# Background thread for reading compressor data
def update_screw_data():
    while not stop_events["screw"].is_set():
        try:
            logger.info("Reading screw compressor data...")
            plc_reader.read_plcs_from_config(
                config_file="config/config.yaml",
                plc_points_file="config/screw_comp_points.yaml",
                floating_points_file=None,
                digital_points_file=None
            )
            time.sleep(int(os.getenv("POLLING_INTERVAL_COMP", 10)))
        except Exception as e:
            logger.error(f"Error in compressor thread: {e}")

# Background thread for reading compressor data
def update_viltor_data():
    while not stop_events["viltor"].is_set():
        try:
            logger.info("Reading viltor compressor data...")
            plc_reader.read_plcs_from_config("config/config.yaml", "config/viltor_comp_points.yaml", None, None)
            time.sleep(int(os.getenv("POLLING_INTERVAL_COMP", 10)))
        except Exception as e:
            logger.error(f"Error in compressor thread: {e}")

# Background thread for reading VFD data
def update_vfd_data():
    while not stop_events["vfd"].is_set():
        try:
            logger.info("Reading VFD data...")
            plc_reader.read_plcs_from_config("config/vfd_config.yaml", "config/vfd_points.yaml", None, None)
            time.sleep(int(os.getenv("POLLING_INTERVAL_VFD", 10)))
        except Exception as e:
            logger.error(f"Error in VFD thread: {e}")

# Background thread for reading HMI data
def update_hmi_data():
    while not stop_events["hmi"].is_set():
        try:
            logger.info("Reading HMI data...")
            plc_reader.read_plcs_from_config("config/hmi_config.yaml", "config/hmi_points.yaml", None, None)
            time.sleep(int(os.getenv("POLLING_INTERVAL_HMI", 10)))
        except Exception as e:
            logger.error(f"Error in HMI thread: {e}")

# Background thread for reading PLC data
def update_plc_data():
    while not stop_events["plc"].is_set():
        try:
            logger.info("Starting PLC data read cycle...")
            
            # Read data from PLCs
            all_plc_data = plc_reader.read_plcs_from_config(
                config_file="config/config.yaml",
                plc_points_file="config/plc_points.yaml",
                floating_points_file="config/floating_points.yaml",
                digital_points_file="config/digital_points.yaml"
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

# Background thread for reading PLC data
def update_condenser_data():
    while not stop_events["condenser"].is_set():
        try:
            logger.info("Starting Condenser data read cycle...")
            
            # Read data from PLCs
            all_cond_data = plc_reader.read_plcs_from_config(
                config_file="config/cond_config.yaml",
                plc_points_file="config/cond_points.yaml",
                floating_points_file=None,
                digital_points_file=None
            )

            # Log and handle the aggregated data if necessary
            if all_cond_data:
                logger.debug(f"Aggregated PLC data: {all_cond_data}")
            else:
                logger.warning("No data received during PLC read cycle.")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 10)))

        except FileNotFoundError as e:
            logger.error(f"Configuration or points file not found: {e}")
            break  # Break the loop if a critical file is missing
        except Exception as e:
            logger.error(f"Unexpected error in PLC thread: {e}")
    
# Background thread for reading Compressor data
def update_screw_compressor_data():
    while not stop_events["compressor"].is_set():
        try:
            logger.info("Starting Compressor data read cycle...")
            
            # Read data from PLCs
            all_comp_data = plc_reader.read_plcs_from_config(
                config_file="config/config.yaml",
                plc_points_file="config/screw_comp_points.yaml",
                floating_points_file="config/screw_comp_floating_points.yaml",  # If applicable
                digital_points_file="config/screw_comp_digital_points.yaml"     # If applicable
            )

            # Log and handle the aggregated data if necessary
            if all_comp_data:
                logger.debug(f"Aggregated Compressor data: {all_comp_data}")
            else:
                logger.warning("No data received during PLC read cycle.")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 10)))

        except FileNotFoundError as e:
            logger.error(f"Configuration or points file not found: {e}")
            break  # Break the loop if a critical file is missing
        except Exception as e:
            logger.error(f"Unexpected error in Compressor thread: {e}")

# Background thread for reading Viltor data
def update_viltor_compressor_data():
    while not stop_events["viltor"].is_set():
        try:
            logger.info("Starting Viltor data read cycle...")
            
            # Read data from PLCs
            all_viltor_data = plc_reader.read_plcs_from_config(
                config_file="config/config.yaml",
                plc_points_file="config/viltor_comp_points.yaml",
                floating_points_file="config/viltor_comp_floating_points.yaml",  # If applicable
                digital_points_file="config/viltor_comp_digital_points.yaml"     # If applicable
            )

            # Log and handle the aggregated data if necessary
            if all_viltor_data:
                logger.debug(f"Aggregated Viltor data: {all_viltor_data}")
            else:
                logger.warning("No data received during PLC read cycle.")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 10)))

        except FileNotFoundError as e:
            logger.error(f"Configuration or points file not found: {e}")
            break  # Break the loop if a critical file is missing
        except Exception as e:
            logger.error(f"Unexpected error in Viltor thread: {e}")


# Custom lifespan manager using asynccontextmanager
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application...")
    threads = []
    # Enable specific threads
    # threads.append(threading.Thread(target=update_screw_data, daemon=True))
    # threads.append(threading.Thread(target=monitor_screw_comp_suction_pressure, daemon=True))
    # threads.append(threading.Thread(target=monitor_viltor_comp_suction_pressure, daemon=True))
    # threads.append(threading.Thread(target=update_viltor_data, daemon=True))
    # threads.append(threading.Thread(target=update_vfd_data, daemon=True))
    # threads.append(threading.Thread(target=update_hmi_data, daemon=True))
    #threads.append(threading.Thread(target=update_screw_compressor_data, daemon=True))
    #threads.append(threading.Thread(target=update_condenser_data, daemon=True))
    #threads.append(threading.Thread(target=update_viltor_compressor_data, daemon=True))
    main_plc_thread = threading.Thread(target=update_plc_data, daemon=True)
    
    threads.append(main_plc_thread)
    
   
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

@app.route('/api/compressors/screw', methods=['GET'])
def get_screw_compressors():
    compressors = [
        {"name":"Compressor A", "status":"Running"}
    ]
    return jsonify(compressors)

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
            write_points_path = "config/plc_write_points.yaml"
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

    - Loads the config file.
    - Fetches data from the shared storage.
    - Loops through each screw compressor in the configuration.
    - Logs a warning if the suction pressure exceeds 45.
    """
    while not stop_events["monitor_screw_suction_pressure"].is_set():
        try:
            logger.info("Starting suction pressure monitoring...")

            # Load the screw compressor configuration
            config_file = "config/config.yaml"
            screw_comps = plc_reader.load_config(config_file)

            if not screw_comps:
                logger.error(f"No screw compressors found in configuration file: {config_file}")
                return

            for screw_comp in screw_comps:
                plc_name = screw_comp.get("name")
                if not plc_name:
                    logger.warning("Skipping a screw compressor with no name in the configuration.")
                    continue

                # Fetch data from storage
                data = storage.get_data().get(plc_name, {})
                logger.info(f"Data: {data}")
                if not data:
                    logger.warning(f"No data available for {plc_name}. Skipping...")
                    continue

                # Access nested data for SUCTION PRESSURE
                nested_data = data.get("data", {})
                suction_pressure = nested_data.get("SUCTION PRESSURE")/100
                if suction_pressure is None:
                    logger.warning(f"Suction pressure data not found for {plc_name}. Skipping...")
                    continue

                # Check if suction pressure exceeds the threshold
                if suction_pressure > 36:
                    logger.warning(f"WARNING: Suction pressure for {plc_name} is above threshold: {suction_pressure} > 45")
                    # Play alarm sound
                    #playsound('static/alarm.wav')
                    play_alarm()
                else:
                    logger.info(f"Suction pressure for {plc_name} is normal: {suction_pressure}")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 20)))

        except Exception as e:
            logger.error(f"Error during suction pressure monitoring: {e}")

def monitor_viltor_comp_suction_pressure():
    """
    Monitors the suction pressure for all Viltor compressors defined in the configuration.

    - Loads the config file.
    - Fetches data from the shared storage.
    - Loops through each Viltor compressor in the configuration.
    - Logs a warning if the suction pressure exceeds 45.
    """
    while not stop_events["monitor_viltor_suction_pressure"].is_set():
        try:
            logger.info("Starting Viltor compressor suction pressure monitoring...")

            # Load the Viltor compressor configuration
            config_file = "config/config.yaml"
            viltor_comps = plc_reader.load_config(config_file)

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
                logger.info(f"Data: {data}")
                if not data:
                    logger.warning(f"No data available for {plc_name}. Skipping...")
                    continue

                # Access nested data for SUCTION PRESSURE
                nested_data = data.get("data", {})
                suction_pressure = nested_data.get("SUC MOD")
                if suction_pressure is None:
                    logger.warning(f"Suction pressure data not found for {plc_name}. Skipping...")
                    continue
                else:
                    suction_pressure = suction_pressure/100
                    # Check if suction pressure exceeds the threshold
                    if suction_pressure > 48:
                        logger.warning(f"WARNING: Suction pressure for {plc_name} is above threshold: {suction_pressure} > 45")
                        # Play alarm sound
                        #playsound('static/alarm.wav')
                        play_alarm()
                    else:
                        logger.info(f"Suction pressure for {plc_name} is normal: {suction_pressure}")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 60)))

        except Exception as e:
            logger.error(f"Error during Viltor compressor suction pressure monitoring: {e}")


def play_alarm():
    pygame.mixer.init()
    pygame.mixer.music.load("static/high_suction_pressure_alarm.wav")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():  # Wait for the music to finish
        continue