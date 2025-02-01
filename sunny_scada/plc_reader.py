from pymodbus.client import ModbusTcpClient
import yaml
import logging
import struct

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PLCReader:
    def __init__(self, storage=None):
        # Central data storage
        self.storage = storage
        try:
            self.data_points = self.load_data_points()
            self.config_data = self.load_config()

            # Initialize Modbus clients for each PLC
            self.clients = {
                plc["name"]: ModbusTcpClient(plc["ip"], port=plc["port"]) for plc in self.config_data.get("plcs", [])
            }
            logger.info(f"Initialized PLCReader for plc with {len(self.clients)} clients.")
        except Exception as e:
            logger.error(f"Error initializing PLCReader: {e}")
            raise

    def load_config(self, config_file="config/plc_config.yaml"):
        """Load the PLC configuration from a YAML file."""
        try:
            with open(config_file, 'r') as file:
                config = yaml.safe_load(file)
            if not isinstance(config, dict):
                raise ValueError("Configuration file must contain a dictionary structure.")
            
            valid_keys = {"plcs"}
            config_data = {key: config[key] for key in valid_keys if key in config}
            if not config_data:
                raise ValueError("Configuration file contains no valid keys.")
            return config_data
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {config_file}")
            raise
        except Exception as e:
            logger.error(f"Error loading configuration file {config_file}: {e}")
            raise

    def load_data_points(self, points_file="config/data_points.yaml"):
        """Load data points from a YAML file."""
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

    
    def close_clients(self):
        """
        Closes all Modbus clients.
        """
        if hasattr(self, "clients"):
            for name, client in self.clients.items():
                if client.is_socket_open():
                    logger.info(f"Closing Modbus client for device '{name}'")
                    client.close()

    def connect_to_client(self, client, plc_name, plc_ip):
        """Attempts to connect to the Modbus client."""
        if client.connect():
            return True
        logger.error(f"Failed to connect to {plc_name} at {plc_ip}")
        return False

    def is_valid_response(self, response):
        """Checks if the Modbus response is valid."""
        return response and not response.isError()

    def scale_value(self, raw_value, raw_zero, raw_full, eng_zero, eng_full):
        """Scales the raw Modbus value to engineering units."""
        if all(v is not None for v in [raw_zero, raw_full, eng_zero, eng_full]):
            return ((raw_value - raw_zero) / (raw_full - raw_zero)) * (eng_full - eng_zero) + eng_zero
        #logger.warning("Missing scaling parameters. Using raw value.")
        return raw_value

    def convert_to_float(self, higher_register, low_register):
        """Converts two consecutive Modbus registers into an IEEE-754 32-bit float."""
        try:
            combined = (higher_register << 16) | low_register
            return struct.unpack('>f', struct.pack('>I', combined))[0]
        except Exception as e:
            logger.error(f"Error converting to float: {e}")
            return None

    def read_plc(self, plc, client, data_points, parent_key=""):
        """Reads data from a single PLC and maps values to descriptive names."""
        if not self.connect_to_client(client, plc['name'], plc['ip']):
            return None

        plc_data = {}
        try:
            for point_name, point_details in data_points.items():
                if isinstance(point_details, dict) and "address" not in point_details:
                    nested_data = self.read_plc(plc, client, point_details, point_name)
                    plc_data[point_name] = nested_data
                    continue

                data = self.read_data_point(client, point_name, point_details)
                if data:
                    plc_data[point_name] = data
        except Exception as e:
            logger.error(f"Error reading from {plc['name']} at {plc['ip']}: {e}")
        finally:
            client.close()

        return plc_data

    def read_data_point(self, client, point_name, point_details):
        """Reads a specific data point based on its type."""
        try:
            
            #logger.info(f"Reading data point '{point_name}'...")
            #logger.info(f"Point details: {point_details}")
            address = point_details.get("address")
            data_type = point_details.get("type")
            description = point_details.get("description")
            monitor = point_details.get("monitor")
            process = point_details.get("process")
            min_value = point_details.get("min")
            min_audio = point_details.get("min_audio")
            max_value = point_details.get("max")
            max_audio = point_details.get("max_audio")

            if not address or not data_type:
                logger.warning(f"Invalid data point configuration for '{point_name}'. Skipping...")
                return None

            register_address = address - 40001 +1
            #logger.info(f"Reading data point '{point_name}' at address {register_address}...")
            if data_type == "INTEGER":
                return self.read_integer(
                    client, register_address, point_name, description, monitor, process, min_value, max_value, max_audio, min_audio
                )
            elif data_type == "REAL":
                return self.read_real(
                    client, register_address, point_name, description, point_details, monitor, process, min_value, max_value, max_audio, min_audio
                )
            elif data_type == "DIGITAL":
                return self.read_digital(
                    client, register_address, point_name, description, point_details, monitor, process, min_value, max_value, max_audio, min_audio
                )
            else:
                logger.error(f"Unsupported data type '{data_type}' for point '{point_name}'.")
                return None
        except Exception as e:
            logger.error(f"Error processing data point '{point_name}': {e}")
            return None

    def read_integer(self, client, register_address, point_name, description, monitor, process, min_value, max_value, max_audio, min_audio):
        """Reads an integer data point."""
        response = client.read_holding_registers(register_address, 1)
        #logger.info(f"Response: {response.registers[0]}")
        if self.is_valid_response(response):
            return {
            "description": description,
            "type": "INTEGER",
            "value": response.registers[0],
            "monitor": monitor,
            "process": process,
            "min": min_value,
            "max": max_value,
            "max_audio": max_audio,
            "min_audio": min_audio,
            "register_address": register_address
        }
        else:
            logger.warning(f"Failed to read integer '{point_name}' at address {register_address}.")
            return None

    def read_real(self, client, register_address, point_name, description, point_details, monitor, process, min_value, max_value, max_audio, min_audio):
        """Reads a floating-point (REAL) data point."""
        response = client.read_holding_registers(register_address+1, 2)
        if self.is_valid_response(response):
            high_register, low_register = response.registers
            raw_value = self.convert_to_float(high_register, low_register)
            scaled_value = self.scale_value(
                raw_value,
                point_details.get("raw_zero_scale"),
                point_details.get("raw_full_scale"),
                point_details.get("eng_zero_scale"),
                point_details.get("eng_full_scale"),
            )
            return {
                "description": description,
                "type": "REAL",
                "raw_value": raw_value,
                "scaled_value": scaled_value,
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": register_address
            }
        else:
            logger.warning(f"Failed to read real '{point_name}' at address {register_address}.")
            return None

    def read_digital(self, client, register_address, point_name, description, point_details, monitor, process, min_value, max_value, max_audio, min_audio):
        """Reads a digital data point (single register with bits)."""
        response = client.read_holding_registers(register_address, 1)
        if self.is_valid_response(response):
            # Get the 16-bit integer value
            integer_value = response.registers[0]
            logger.debug(f"Integer value at register {register_address}: {integer_value} (binary: {bin(integer_value)})")

            # Extract individual bits (0 to 15)
            bit_statuses = {}
            for bit_position in range(16):
                bit_value = (integer_value >> bit_position) & 0x01  # Extract the bit
                bit_label = f"BIT {bit_position}"
                description = point_details.get("bits", {}).get(bit_label, "UNKNOWN")
                bit_statuses[bit_label] = {"description": description, "value": bool(bit_value)}
                logger.debug(f"Bit {bit_position}: {bit_value} (Description: {description})")
            return {
                "description": description,
                "type": "DIGITAL",
                "value": bit_statuses,
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": register_address
            }
        else:
            logger.warning(f"Failed to read digital '{point_name}' at address {register_address}.")
            return None

    def read_single_bit(self, client, register_address, bit_position):
        """
        Reads a single bit value from a Modbus register.

        :param client: ModbusTcpClient instance connected to the PLC.
        :param register_address: Address of the Modbus register.
        :param bit_position: Position of the bit in the register (0-based).
        :return: Boolean value of the bit or None if an error occurs.
        """
        try:
            #logger.info(f"Reading bit {bit_position} from register {register_address}...")

            # Adjust the address to account for Modbus 0-based indexing
            adjusted_address = register_address - 40001 + 1

            # Read the single register
            response = client.read_holding_registers(adjusted_address, 1)
            if response and not response.isError():
                # Extract the register value and evaluate the specific bit
                register_value = response.registers[0]
                bit_value = bool(register_value & (1 << bit_position))
                #logger.info(f"Read bit {bit_position} value: {bit_value}")
                return bit_value
            else:
                logger.error(f"Failed to read register {register_address}.")
                return None
        except Exception as e:
            logger.error(f"Error reading bit {bit_position} from register {register_address}: {e}")
            return None
        finally:
            # Safely close the connection to the PLC
            if client:
                client.close()


    def read_plcs_from_config(self, config_file, data_points_file):
        """
        Reads data from all PLCs defined in the configuration file.
        """
        try:
            

            all_device_data = {}
            for section, devices in self.config_data.items():
                #logger.info(f"Reading data from section '{section}'...")
                section_data = {}
                for device in devices:
                    #logger.info(f"Reading data from device '{device['name']}' at {device['ip']}...")
                    client = self.clients.get(device["name"])
                    #logger.info(f"Client: {client}")
                    if not client or not client.connect():
                        logger.error(f"Unable to connect to device '{device['name']}'. Skipping...")
                        continue

                    # Read data from the PLC
                    device_data = self.read_plc(device, client, self.data_points.get(section, {}))
                    section_data[device["name"]] = device_data

                    # Update storage if configured
                    if self.storage:
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
