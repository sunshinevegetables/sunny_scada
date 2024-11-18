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

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize shared components
storage = DataStorage()
plc_reader = PLCReader(storage=storage)

# Initialize shared PLC writer
screw_comp_writer = PLCWriter(config_type="screw_comp")
viltor_comp_writer = PLCWriter(config_type="viltor_comp") 

# Define stop events for threads
stop_events = {
    "screw": threading.Event(),
    "viltor": threading.Event(),
    "vfd": threading.Event(),
    "hmi": threading.Event(),
    "plc": threading.Event(),
    "alarms": threading.Event(),
    "monitor_suction_pressure": threading.Event()}

# Define the model for the request body
class WriteSignalRequest(BaseModel):
    plc_name: str
    plc_type: str  
    signal_name: str
    value: int

# Define the request model
class StartPackhouseRequest(BaseModel):
    plc_name: str


# Background thread for reading compressor data
def update_screw_data():
    while not stop_events["screw"].is_set():
        try:
            logger.info("Reading screw compressor data...")
            plc_reader.read_plcs_from_config(
                config_file="config/screw_comp_config.yaml",
                plc_points_file="config/screw_comp_points.yaml",
                floating_points_file=None
            )
            time.sleep(int(os.getenv("POLLING_INTERVAL_COMP", 10)))
        except Exception as e:
            logger.error(f"Error in compressor thread: {e}")

# Background thread for reading compressor data
def update_viltor_data():
    while not stop_events["viltor"].is_set():
        try:
            logger.info("Reading viltor compressor data...")
            plc_reader.read_plcs_from_config("config/viltor_comp_config.yaml", "config/viltor_comp_points.yaml", None)
            time.sleep(int(os.getenv("POLLING_INTERVAL_COMP", 10)))
        except Exception as e:
            logger.error(f"Error in compressor thread: {e}")

# Background thread for reading VFD data
def update_vfd_data():
    while not stop_events["vfd"].is_set():
        try:
            logger.info("Reading VFD data...")
            plc_reader.read_plcs_from_config("config/vfd_config.yaml", "config/vfd_points.yaml", None)
            time.sleep(int(os.getenv("POLLING_INTERVAL_VFD", 10)))
        except Exception as e:
            logger.error(f"Error in VFD thread: {e}")

# Background thread for reading HMI data
def update_hmi_data():
    while not stop_events["hmi"].is_set():
        try:
            logger.info("Reading HMI data...")
            plc_reader.read_plcs_from_config("config/hmi_config.yaml", "config/hmi_points.yaml", None)
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
                config_file="config/plc_config.yaml",
                plc_points_file="config/plc_points.yaml",
                floating_points_file="config/floating_points.yaml"
            )

            # Log and handle the aggregated data if necessary
            if all_plc_data:
                logger.debug(f"Aggregated PLC data: {all_plc_data}")
            else:
                logger.warning("No data received during PLC read cycle.")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 10)))

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
    threads.append(threading.Thread(target=update_screw_data, daemon=True))
    threads.append(threading.Thread(target=monitor_suction_pressure, daemon=True))
    # threads.append(threading.Thread(target=update_viltor_data, daemon=True))
    # threads.append(threading.Thread(target=update_vfd_data, daemon=True))
    # threads.append(threading.Thread(target=update_hmi_data, daemon=True))
    # main_plc_thread = threading.Thread(target=update_plc_data, daemon=True)
    # threads.append(main_plc_thread)
    
   
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

@app.get("/plc_data", summary="Get PLC Data", description="Fetch the latest data from all configured PLCs.")
def get_plc_data():
    return storage.get_data()

@app.post("/write_signal", summary="Write a Signal to PLC", description="Send a signal to a specific PLC.")
def write_signal(request: WriteSignalRequest):
    """
    API endpoint to write a signal to a PLC.
    """
    try:
        # Determine the write points YAML file based on plc_type
        if request.plc_type == "screw_comp":
            write_points_path = "config/screw_comp_write_points.yaml"
        elif request.plc_type == "viltor_comp":
            write_points_path = "config/viltor_comp_write_points.yaml"
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
            success = screw_comp_writer.write_signal(request.plc_name, request.signal_name, request.value)
        elif request.plc_type == "viltor_comp":
            success = viltor_comp_writer.write_signal(request.plc_name, request.signal_name, request.value)
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
        suction_pressure_address = 40001  # Define the address if needed
        if not monitor_suction_pressure(
            plc_name=plc_name,
            address=suction_pressure_address,
            retries=10,
            interval=5
        ):
            raise HTTPException(status_code=500, detail="Suction pressure did not stabilize in time.")

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

def monitor_suction_pressure():
    """
    Monitors the suction pressure for all screw compressors defined in the configuration.

    - Loads the screw_comp_config file.
    - Fetches data from the shared storage.
    - Loops through each screw compressor in the configuration.
    - Logs a warning if the suction pressure exceeds 45.
    """
    while not stop_events["monitor_suction_pressure"].is_set():
        try:
            logger.info("Starting suction pressure monitoring...")

            # Load the screw compressor configuration
            config_file = "config/screw_comp_config.yaml"
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
                if suction_pressure > 45:
                    logger.warning(f"WARNING: Suction pressure for {plc_name} is above threshold: {suction_pressure} > 45")
                    # Play alarm sound
                    playsound('assets/alarm.mp3')
                else:
                    logger.info(f"Suction pressure for {plc_name} is normal: {suction_pressure}")

            # Wait for the next polling interval
            time.sleep(int(os.getenv("POLLING_INTERVAL_PLC", 20)))

        except Exception as e:
            logger.error(f"Error during suction pressure monitoring: {e}")

    
