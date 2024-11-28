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
            {logger.info(f"Initializing Clinet {plc['name']} ::: {plc['port']} ::: {plc['ip']}") for plc in config}
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
                    
                    logger.debug(f"Modbus Address::: {modbus_address} || Register Address::: {register_address} ||| Description ::: {description}")

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

    

    def read_plcs_from_config(self, config_file, plc_points_file=None, floating_points_file=None, digital_points_file=None):
        """
        Reads data from all PLCs, compressors, condensers, etc., defined in the specified configuration file.

        :param config_file: Path to the consolidated configuration file (e.g., config.yaml).
        :param plc_points_file: Path to the integer data points file.
        :param floating_points_file: Path to the floating-point data points file.
        :param digital_points_file: Path to the digital data points file.
        :return: Dictionary containing combined data read from PLCs, keyed by device type and name.
        """
        try:
            # Load the consolidated configuration
            config_data = self.load_config(config_file)
            logger.debug(f"Read PLC FROM CONFIG DATA:: {config_data}")
            if not config_data:
                raise ValueError("Configuration file is empty or invalid.")

            # Ensure the config contains valid sections
            valid_sections = {"viltor_comp", "screw_comp", "evap_cond", "hmis", "vfds", "plcs"}
            if not valid_sections.intersection(config_data.keys()):
                raise ValueError(f"Invalid configuration file format. Expected one of {valid_sections}.")

            # Load data points
            data_points = self.load_data_points(plc_points_file) if plc_points_file else {}
            #logger.info(f"Data Points: {data_points}")
            #floating_points = self.load_data_points(floating_points_file) if floating_points_file else {}
            #logger.info(f"Floating Points: {floating_points}")
            #digital_points = self.load_data_points(digital_points_file) if digital_points_file else {}
            #logger.info(f"Digital Points: {digital_points}")

            # Initialize Modbus clients for all devices
            devices = []
            for key in valid_sections:
                devices += config_data.get(key, [])
            logger.debug(f"Devices:: {devices}")
            clients = self.initialize_clients(devices)
            if not clients:
                logger.error("Failed to initialize Modbus clients.")
                return None

            all_device_data = {}

            # Iterate through sections in the configuration
            for section, devices in config_data.items():
                logger.info(f"################# Section:{section} ####################")
                if section not in valid_sections:
                    continue

                section_data = {}
                for device in devices:
                    client = clients.get(device["name"])
                    logger.info(f":::::::::::::: {client} :::::::::::::::::::")
                    if not client:
                        logger.error(f"No client found for device '{device['name']}'. Skipping...")
                        continue

                    logger.debug(f"Reading data points for {section} '{device['name']}' at {device['ip']}...")
                    int_data = self.read_plc(device, client, data_points, None, None)
                    float_data = self.read_plc(device, client, None, None, None)
                    digital_data = self.read_plc(device, client, None, None, None)

                    # Combine data
                    combined_data = {**int_data, **float_data, **digital_data}
                    section_data[device["name"]] = combined_data

                    # Update storage
                    self.storage.update_data(device["name"], combined_data)

                all_device_data[section] = section_data

            return all_device_data

        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {e}")
            return None
        except ValueError as e:
            logger.error(f"Error loading configuration file {config_file}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error while processing configuration file {config_file}: {e}")
            return None





