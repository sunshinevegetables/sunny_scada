from pymodbus.client import ModbusTcpClient
import yaml
import logging
from sunny_scada.data_storage import DataStorage
import struct

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PLCReader:
    def __init__(self, storage=None):
        # Central data storage
        self.storage = storage
        self.data_points = {}  # Will be dynamically loaded

    def load_config(self, config_file):
        """
        Loads the PLC configuration from the YAML file.
        
        :param config_file: Path to the configuration file
        :return: Parsed configuration data
        """
        try:
            with open(config_file, 'r') as file:
                config = yaml.safe_load(file)

            # Identify the correct key based on configuration type
            if "screw_comp" in config:
                return config["screw_comp"]
            elif "viltor_comp" in config:
                return config["viltor_comp"]
            elif "hmis" in config:
                return config["hmis"]
            elif "vfds" in config:
                return config["vfds"]
            elif "plc" in config:
                return config["plc"]
            elif "evap_cond" in config:
                return config["evap_cond"]
            else:
                raise ValueError("Invalid configuration file format. Expected 'evap_cond', 'screw_comp', 'viltor_comp', 'hmis', 'vfds', or 'plcs'.")
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {config_file}")
            raise
        except Exception as e:
            logger.error(f"Error loading configuration file {config_file}: {e}")
            raise

    def load_data_points(self, points_file):
        """
        Loads data points from a YAML file.
        
        :param points_file: Path to the data points YAML file
        :return: Dictionary of data points
        """
        try:
            with open(points_file, 'r') as file:
                data = yaml.safe_load(file)
            return data.get("data_points", {})
        except FileNotFoundError:
            logger.error(f"Data points file not found: {points_file}")
            raise
        except Exception as e:
            logger.error(f"Error loading data points file {points_file}: {e}")
            raise

    def initialize_clients(self, config):
        """
        Initialize Modbus clients for the provided configuration.

        :param config: List of PLC configurations
        :return: Dictionary of PLC clients
        """
        try:
            return {plc['name']: ModbusTcpClient(plc['ip'], port=plc['port']) for plc in config}
        except KeyError as e:
            logger.error(f"Missing key in PLC configuration: {e}")
            raise
        except Exception as e:
            logger.error(f"Error initializing clients: {e}")
            raise

    def convert_to_float(self, high_register, low_register):
        """
        Converts two consecutive Modbus register values into an IEEE-754 floating-point number.

        :param high_register: The high 16-bit register value.
        :param low_register: The low 16-bit register value.
        :return: The floating-point representation of the combined registers, or None if invalid.
        """
        try:
            # Validate register values
            if not (0 <= high_register <= 65535 and 0 <= low_register <= 65535):
                logger.warning(f"Invalid register values: high={high_register}, low={low_register}")
                return None

            # Combine high and low registers into a 32-bit integer
            combined = (high_register << 16) | low_register

            # Convert the 32-bit integer into IEEE-754 floating-point format
            float_value = struct.unpack('>f', combined.to_bytes(4, byteorder='big'))[0]
            return float_value
        except Exception as e:
            logger.error(f"Error converting registers {high_register}, {low_register} to float: {e}")
            return None

    def read_plc(self, plc, client, data_points=None, floating_points=None, digital_points=None):
        """
        Reads data from a single PLC and maps the values to descriptive names.

        :param plc: PLC configuration dictionary.
        :param client: ModbusTcpClient instance for the PLC.
        :param data_points: Dictionary of data points for the PLC type.
        :param floating_points: Dictionary of floating-point data points (optional).
        :return: Dictionary of read data.
        """
        if not client.connect():
            logger.error(f"Failed to connect to {plc['name']} at {plc['ip']}")
            return None

        plc_data = {}
        try:
            if data_points:
                # Read all required data points
                for description, modbus_address in data_points.items():
                    register_address = modbus_address - 40001  # Adjust for pymodbus 0-based indexing
                    response = client.read_holding_registers(register_address, 1)
                    if response and not response.isError():
                        plc_data[description] = response.registers[0]
                    else:
                        logger.warning(f"Failed to read {description} ({modbus_address}) from {plc['name']}")

            # Process floating-point data points if provided
            if floating_points:
                for description, modbus_address in floating_points.items():
                    try:
                        high_register_address = modbus_address - 40001
                        response = client.read_holding_registers(high_register_address, 2)
                        if response and not response.isError():
                            high_register, low_register = response.registers

                            # Log raw register values before conversion
                            logger.debug(
                                f"Reading {description} ({modbus_address}): High Register={high_register}, Low Register={low_register}"
                            )

                            float_value = self.convert_to_float(high_register, low_register)
                            if float_value is not None:
                                logger.debug(f"Converted {description} ({modbus_address}) to float: {float_value}")
                                plc_data[description] = float_value
                            else:
                                logger.warning(f"Failed to convert {description} ({modbus_address}) to float.")
                        else:
                            logger.warning(f"Failed to read {description} ({modbus_address}) from {plc['name']}")
                    except Exception as e:
                        logger.error(f"Error reading {description} ({modbus_address}) from {plc['name']}: {e}")
            # Read digital signals
            if digital_points:
                for description, details in digital_points.items():
                    address = details.get("address")
                    alarms = details.get("alarms", [])

                    if address is None or not alarms:
                        logger.warning(f"No valid address or alarms defined for {description}")
                        continue

                    coil_address = address - 40001  # Adjust for pymodbus 0-based indexing
                    
                    # Read the coil/register value for the alarms
                    response = client.read_holding_registers(coil_address, 1)
                    if response and not response.isError():
                        register_value = response.registers[0]

                        # Parse individual alarm bits
                        alarm_statuses = {}
                        for alarm in alarms:
                            bit = alarm.get("bit")
                            alarm_description = alarm.get("description")

                            if bit is not None and alarm_description:
                                # Evaluate bit status (0 or 1)
                                bit_status = bool(register_value & (1 << bit))
                                alarm_statuses[alarm_description] = bit_status
                                logger.info(f"{description} - {alarm_description}: {'ON' if bit_status else 'OFF'}")
                            else:
                                logger.warning(f"Invalid alarm configuration for {description}: {alarm}")

                        plc_data[description] = alarm_statuses
                    else:
                        logger.warning(f"Failed to read digital signal {description} ({address}) from PLC.")

        except Exception as e:
            logger.error(f"Error reading from {plc['name']} at {plc['ip']}: {e}")
        finally:
            client.close()

        return plc_data

    

    def read_plcs_from_config(self, config_file, plc_points_file, floating_points_file, digital_points_file):
            """
            Reads data from all PLCs defined in the specified configuration file using both integer and floating-point data points.

            :param config_file: Path to the PLC configuration file
            :param plc_points_file: Path to the integer data points file
            :param floating_points_file: Path to the floating-point data points file
            :return: Dictionary containing combined data read from PLCs, keyed by PLC name
            """
            logger.info(f"Config File: {config_file}")
            logger.info(f"Points File: {plc_points_file}")
            logger.info(f"Points File: {floating_points_file}")
            logger.info(f"Points File: {digital_points_file}")
            try:
                # Load PLC configuration
                plc_config = self.load_config(config_file)
                logger.debug(f"Configurations: {plc_config}")
                if not plc_config:
                    logger.error(f"PLC configuration file is empty or invalid: {config_file}")
                    return None

                # Load integer-based data points
                data_points = self.load_data_points(plc_points_file)
                logger.debug(f"Data Points: {data_points}")
                if not data_points:
                    logger.error(f"Data points file is empty or invalid: {plc_points_file}")
                    return None
                floating_points={}
                # Load floating-point data points
                if floating_points_file:
                    floating_points = self.load_data_points(floating_points_file)
                    if not floating_points:
                        logger.warning(f"Floating points file is empty or invalid: {floating_points_file}")
                        floating_points = {}  # Fallback to an empty dictionary

                digital_points={}
                # Load floating-point data points
                if digital_points_file:
                    digital_points = self.load_data_points(digital_points_file)
                    if not digital_points:
                        logger.warning(f"Digital points file is empty or invalid: {digital_points_file}")
                        digital_points = {}  # Fallback to an empty dictionary

                # Initialize Modbus clients
                clients = self.initialize_clients(plc_config)
                if not clients:
                    logger.error("Failed to initialize Modbus clients.")
                    return None

                # Dictionary to store combined PLC data
                all_plc_data = {}

                # Read data for integer points
                for plc in plc_config:
                    client = clients.get(plc["name"])
                    if client is None:
                        logger.error(f"No client found for PLC '{plc['name']}'. Skipping...")
                        continue

                    logger.info(f"Reading integer data points from PLC '{plc['name']}' at IP {plc['ip']}...")
                    int_data = self.read_plc(plc, client, data_points, None, None)
                    #logger.debug(f"Integer data read from '{plc['name']}': {int_data}")

                    logger.info(f"Reading floating-point data points from PLC '{plc['name']}' at IP {plc['ip']}...")
                    float_data = self.read_plc(plc, client, None, floating_points, None)
                    #logger.debug(f"Floating-point data read from '{plc['name']}': {float_data}")
                    
                    logger.info(f"Reading digital data points from PLC '{plc['name']}' at IP {plc['ip']}...")
                    float_data = self.read_plc(plc, client, None, None, digital_points)
                    #logger.debug(f"Floating-point data read from '{plc['name']}': {float_data}")

                    # Combine integer and floating-point data
                    combined_data = {**int_data, **float_data}
                    all_plc_data[plc["name"]] = combined_data

                    # Update storage with combined data
                    self.storage.update_data(plc["name"], combined_data)

                return all_plc_data

            except FileNotFoundError as e:
                logger.error(f"Configuration file not found: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error while processing configuration file {config_file}: {e}")
                return None



