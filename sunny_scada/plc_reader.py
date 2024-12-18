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
        :return: Parsed configuration data grouped by type (compressors, evap_cond, hmis, vfds, plcs)
        """
        try:
            with open(config_file, 'r') as file:
                config = yaml.safe_load(file)

            # Validate and return the full configuration structure
            if not isinstance(config, dict):
                raise ValueError("Configuration file must contain a dictionary structure.")

            valid_keys = {"screw_comp", "viltor_comp", "evap_cond", "hmis", "vfds", "plcs"}
            config_data = {}

            for key in valid_keys:
                if key in config:
                    config_data[key] = config[key]

            if not config_data:
                raise ValueError("Configuration file contains no valid keys.")
            logger.debug(f"Config Data: {config_data}")
            return config_data
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
            {logger.debug(f"Initializing Clinet {plc['name']} ::: {plc['port']} ::: {plc['ip']}") for plc in config}
            return {plc['name']: ModbusTcpClient(plc['ip'], port=plc['port']) for plc in config}
        except KeyError as e:
            logger.error(f"Missing key in PLC configuration: {e}")
            raise
        except Exception as e:
            logger.error(f"Error initializing clients: {e}")
            raise

    def convert_to_float(self,higher_register, low_register):
        """
        Converts two consecutive Modbus registers into an IEEE-754 32-bit floating-point number.
        
        :param higher_register: High 16-bit register value.
        :param low_register: Low 16-bit register value.
        :return: Floating-point value represented by the combined registers.
        """
        try:
            # Combine high and low registers into a 32-bit integer
            combined = (higher_register << 16) | low_register
            
            # Convert to IEEE-754 float
            float_value = struct.unpack('>f', struct.pack('>I', combined))[0]
            return float_value
        except Exception as e:
            print(f"Error converting to float: {e}")
            return None


    def read_plc(self, plc, client, data_points, parent_key=""):
        """
        Reads data from a single PLC and maps the values to descriptive names.

        :param plc: PLC configuration dictionary.
        :param client: ModbusTcpClient instance for the PLC.
        :param data_points: Dictionary of consolidated data points or nested structures.
        :param parent_key: Key of the parent section for hierarchical grouping.
        :return: Dictionary of read data with structured values.
        """
        if not client.connect():
            logger.error(f"Failed to connect to {plc['name']} at {plc['ip']}")
            return None

        plc_data = {}
        try:
            for point_name, point_details in data_points.items():
                if isinstance(point_details, dict) and "address" not in point_details:
                    # Nested structure, recurse
                    logger.debug(f"Processing nested data point group: {point_name}")
                    nested_data = self.read_plc(plc, client, point_details, point_name)
                    plc_data[point_name] = nested_data
                    continue

                # Process individual data point
                address = point_details.get("address")
                data_type = point_details.get("type")
                description = point_details.get("description")
                if not address or not data_type:
                    logger.warning(f"Invalid data point configuration for '{point_name}'. Skipping...")
                    continue

                register_address = address - 40001 + 2  # Adjust for pymodbus 0-based indexing

                if data_type == "INTEGER":
                    # Read integer value
                    response = client.read_holding_registers(register_address, 1)
                    if response and not response.isError():
                        value = response.registers[0]
                        plc_data[point_name] = {
                            "description": description,
                            "type": data_type,
                            "value": value
                        }
                    else:
                        logger.debug(f"Failed to read integer '{point_name}' ({address}) from {plc['name']}")
                
                elif data_type == "REAL":
                    # Read floating-point value (2 registers)
                    response = client.read_holding_registers(register_address, 2)
                    if response and not response.isError():
                        try:
                            high_register, low_register = response.registers
                            raw_value = self.convert_to_float(high_register, low_register)

                            # Fetch scaling details from the point_details
                            raw_zero_scale = point_details.get("raw_zero_scale")
                            raw_full_scale = point_details.get("raw_full_scale")
                            eng_zero_scale = point_details.get("eng_zero_scale")
                            eng_full_scale = point_details.get("eng_full_scale")
                            scale = point_details.get("scale")

                            # Perform scaling if the scales are provided
                            if all(v is not None for v in [raw_zero_scale, raw_full_scale, eng_zero_scale, eng_full_scale]):
                                scaled_value = (((raw_value - raw_zero_scale) / (raw_full_scale - raw_zero_scale)) * \
                                            (eng_full_scale - eng_zero_scale) + eng_zero_scale)
                            else:
                                logger.debug(f"Missing scaling parameters for '{point_name}'. Using raw value.")
                                scaled_value = raw_value
                            # Apply scale if provided
                            if scale is not None:
                                try:
                                    scaled_value *= float(scale)
                                except ValueError as e:
                                    logger.warning(f"Invalid scale '{scale}' for '{point_name}'. Using unscaled value.")
                            # Store the scaled value in the PLC data
                            plc_data[point_name] = {
                                "description": description,
                                "type": data_type,
                                "raw_value": raw_value,
                                "scaled_value": scaled_value,
                                "higher_register": high_register,
                                "low_register": low_register
                            }

                            logger.debug(f"Read REAL data point '{point_name}' with raw value: {raw_value}, scaled value: {scaled_value}")

                        except Exception as e:
                            logger.error(f"Error processing REAL data point '{point_name}': {e}")
                    else:
                        logger.warning(f"Failed to read real '{point_name}' ({address}) from {plc['name']}")

                
                elif data_type == "DIGITAL":
                    # Read digital signal (single register, multiple bits)
                    response = client.read_holding_registers(register_address, 1)
                    if response and not response.isError():
                        register_value = response.registers[0]
                        #logger.info(f"Point Details: {point_details}")
                        
                        # Parse the bit structure from the YAML
                        bits = point_details.get("bits", {})
                        #logger.info(f"BITS: {bits}")
                        
                        bit_statuses = {}
                        for bit_label, bit_description in bits.items():
                            # Extract bit position from the bit label (e.g., "BIT 0")
                            try:
                                bit_position = int(bit_label.replace("BIT ", ""))
                            except ValueError:
                                logger.warning(f"Invalid bit label '{bit_label}' for point '{point_name}'. Skipping...")
                                continue
                            
                            # Evaluate bit status (0 or 1)
                            bit_status = bool(register_value & (1 << bit_position))
                            bit_statuses[bit_label] = {
                                "description": bit_description,
                                "value": bit_status
                            }
                        
                        # Add the parsed digital data to the response
                        plc_data[point_name] = {
                            "description": description,
                            "type": data_type,
                            "value": bit_statuses
                        }
                    else:
                        logger.warning(f"Failed to read digital '{point_name}' ({address}) from {plc['name']}")


        except Exception as e:
            logger.error(f"Error reading from {plc['name']} at {plc['ip']}: {e}")
        finally:
            client.close()

        return plc_data




    def read_plcs_from_config(self, config_file, data_points_file):
        """
        Reads data from all PLCs, compressors, condensers, etc., defined in the specified configuration file.

        :param config_file: Path to the consolidated configuration file (e.g., config.yaml).
        :param data_points_file: Path to the consolidated data points file.
        :return: Dictionary containing combined data read from PLCs, keyed by device type and name.
        """
        try:
            # Load the consolidated configuration and data points
            config_data = self.load_config(config_file)
            #logger.info(f"Config Data: {config_data}")
            data_points = self.load_data_points(data_points_file)
            #logger.info(f"Data Points: {data_points}")
            if not config_data or not data_points:
                raise ValueError("Configuration or data points file is empty or invalid.")

            # Initialize Modbus clients for all devices
            devices = []
            for section_devices in config_data.values():
                devices += section_devices
            clients = self.initialize_clients(devices)
            if not clients:
                logger.error("Failed to initialize Modbus clients.")
                return None

            all_device_data = {}

            # Iterate through sections in the configuration
            for section, devices in config_data.items():
                section_data = {}
                for device in devices:
                    client = clients.get(device["name"])
                    if not client:
                        logger.error(f"No client found for device '{device['name']}'. Skipping...")
                        continue

                    logger.debug(f"Reading data points for {section} '{device['name']}' at {device['ip']}...")
                    device_data = self.read_plc(device, client, data_points.get(section, {}))
                    section_data[device["name"]] = device_data

                    # Update storage
                    self.storage.update_data(device["name"], device_data)

                all_device_data[section] = section_data

            return all_device_data

        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {e}")
            return None
        except ValueError as e:
            logger.error(f"Error loading configuration or data points file: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error while processing configuration or data points file: {e}")
            return None

    def read_single_register(self, plc_name, client, register_details):
        """
        Reads data from a single register based on the provided details.

        :param plc_name: Name of the PLC to read from.
        :param client: ModbusTcpClient instance connected to the PLC.
        :param register_details: Dictionary containing details about the register (address, type, scaling, etc.).
        :return: Dictionary containing the register value and metadata, or None if an error occurs.
        """
        try:
            # Extract required details
            address = register_details.get("address")
            data_type = register_details.get("type")
            description = register_details.get("description")
            raw_zero_scale = register_details.get("raw_zero_scale")
            raw_full_scale = register_details.get("raw_full_scale")
            eng_zero_scale = register_details.get("eng_zero_scale")
            eng_full_scale = register_details.get("eng_full_scale")

            if not address or not data_type:
                logger.error(f"Invalid register details: {register_details}")
                return None

            register_address = address - 40000  # Adjust for pymodbus 0-based indexing

            if data_type == "INTEGER":
                # Read integer value
                response = client.read_holding_registers(register_address, 1)
                if response and not response.isError():
                    value = response.registers[0]
                    return {
                        "description": description,
                        "type": data_type,
                        "value": value
                    }
                else:
                    logger.error(f"Failed to read integer register at address {address}.")
                    return None

            elif data_type == "REAL":
                # Read floating-point value (2 registers)
                response = client.read_holding_registers(register_address, 2)
                if response and not response.isError():
                    high_register, low_register = response.registers
                    raw_value = self.convert_to_float(high_register, low_register)

                    # Perform scaling if scaling parameters are provided
                    if all(v is not None for v in [raw_zero_scale, raw_full_scale, eng_zero_scale, eng_full_scale]):
                        scaled_value = ((raw_value - raw_zero_scale) / (raw_full_scale - raw_zero_scale)) * \
                                    (eng_full_scale - eng_zero_scale) + eng_zero_scale
                    else:
                        logger.warning(f"Missing scaling parameters for REAL register at address {address}. Using raw value.")
                        scaled_value = raw_value

                    return {
                        "description": description,
                        "type": data_type,
                        "raw_value": raw_value,
                        "scaled_value": scaled_value,
                        "higher_register": high_register,
                        "low_register": low_register
                    }
                else:
                    logger.error(f"Failed to read REAL register at address {address}.")
                    return None

            elif data_type == "DIGITAL":
                # Read digital value (single register, multiple bits)
                response = client.read_holding_registers(register_address, 1)
                if response and not response.isError():
                    register_value = response.registers[0]
                    bits = register_details.get("bits", {})
                    bit_statuses = {}
                    for bit_label, bit_description in bits.items():
                        try:
                            bit_position = int(bit_label.replace("BIT ", ""))
                        except ValueError:
                            logger.warning(f"Invalid bit label '{bit_label}'. Skipping...")
                            continue
                        bit_status = bool(register_value & (1 << bit_position))
                        bit_statuses[bit_label] = {
                            "description": bit_description,
                            "value": bit_status
                        }
                    return {
                        "description": description,
                        "type": data_type,
                        "value": bit_statuses
                    }
                else:
                    logger.error(f"Failed to read DIGITAL register at address {address}.")
                    return None

            else:
                logger.error(f"Unsupported data type '{data_type}' for register at address {address}.")
                return None

        except Exception as e:
            logger.error(f"Error reading single register for PLC '{plc_name}': {e}")
            return None





