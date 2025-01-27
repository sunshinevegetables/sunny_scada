from pymodbus.client import ModbusTcpClient
import yaml
import logging
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PLCWriter:
    def __init__(self):
        """
        Initialize PLCWriter with a configuration type to load the relevant YAML configuration.

        :param config_type: The type of configuration ('comps', 'vfd', etc., or 'plc' for global configuration)
        """
        try:
            self.write_signals, plc_config = self.load_configuration()

            # Initialize Modbus clients for each PLC
            self.clients = {
                plc["name"]: ModbusTcpClient(plc["ip"], port=plc["port"]) for plc in plc_config
            }
            logger.info(f"Initialized PLCWriter for plc with {len(self.clients)} clients.")
        except Exception as e:
            logger.error(f"Error initializing PLCWriter: {e}")
            raise

    def load_configuration(self):
        """
        Load the write signals and PLC configuration from `plc_config.yaml` and `data_points.yaml`.

        :return: Tuple of write_signals dictionary and plc_config list
        """
        try:
            # Define file paths
            data_points_path = os.path.join("config", "data_points.yaml")
            config_path = os.path.join("config", "plc_config.yaml")

            # Check if the required files exist
            if not os.path.exists(data_points_path):
                raise FileNotFoundError(f"Data points file not found: {data_points_path}")
            if not os.path.exists(config_path):
                raise FileNotFoundError(f"PLC configuration file not found: {config_path}")

            # Load the data points
            with open(data_points_path, "r") as file:
                write_signals = yaml.safe_load(file).get("data_points", {})
            if not write_signals:
                raise ValueError(f"No data points found in {data_points_path}")

            # Load the PLC configuration
            with open(config_path, "r") as file:
                plc_config = yaml.safe_load(file).get("plcs", [])
            if not plc_config:
                raise ValueError(f"No PLC configurations found in {config_path}")

            logger.info("Successfully loaded configuration from plc_config.yaml and data_points.yaml.")
            return write_signals, plc_config

        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise

    def connect_to_client(self, client, plc_name):
        """Connect to the Modbus client."""
        if client.connect():
            return True
        logger.error(f"Failed to connect to PLC '{plc_name}'")
        return False

    def bit_write_signal(self, client, register_address, bit_position, value):
        """
        Writes a single bit value to a specific position in a Modbus register.

        :param client: ModbusTcpClient instance connected to the PLC.
        :param register_address: Address of the Modbus register.
        :param bit_position: Position of the bit in the register (0-based).
        :param value: Value to write (1 to set the bit, 0 to clear the bit).
        :return: True if successful, False otherwise.
        """
        try:
            logger.info(f"Writing bit {bit_position} with value {value} to register {register_address}...")

            # Adjust the address for Modbus 0-based indexing
            adjusted_address = register_address - 40001 +1

            # Ensure the client is connected
            if not client.connect():
                logger.error("Failed to connect to PLC.")
                return False

            # Read the current value of the register
            response = client.read_holding_registers(adjusted_address, 1)
            if response and hasattr(response, "registers") and response.registers:
                current_value = response.registers[0]
                logger.debug(f"Current value of register {register_address}: {current_value}")

                # Modify the specified bit
                if value == 1:
                    new_value = current_value | (1 << bit_position)  # Set the bit
                else:
                    new_value = current_value & ~(1 << bit_position)  # Clear the bit

                logger.debug(f"Modified value of register {register_address}: {new_value}")

                # Write the modified value back to the register
                write_response = client.write_register(adjusted_address, new_value)
                if write_response and not write_response.isError():
                    logger.info(f"Successfully wrote bit {bit_position} with value {value} to register {register_address}.")
                    return True
                else:
                    logger.error(f"Failed to write modified value {new_value} to register {register_address}.")
                    return False
            else:
                logger.error(f"Failed to read register {register_address} for bit modification.")
                return False
        except Exception as e:
            logger.error(f"Error writing bit {bit_position} to register {register_address}: {e}")
            return False
        finally:
            if client:
                client.close()

   

    def modify_bit(self, current_value, bit_position, value):
        """Modify a specific bit in a register value."""
        if value == 1:
            return current_value | (1 << bit_position)
        return current_value & ~(1 << bit_position)

    def get_modbus_address(self, signal_name):
        """Retrieve the Modbus address for a given signal name."""
        if signal_name not in self.write_signals:
            logger.error(f"Signal '{signal_name}' not found in write mapping.")
            return None
        return self.write_signals[signal_name] - 40000

    def get_register_and_bit(self, write_info):
        """Retrieve the register address and bit position for a signal."""
        register_address = write_info.get("register") - 40000
        bit_position = write_info.get("bit")
        if register_address is None or bit_position is None:
            logger.error(f"Invalid signal mapping: Missing register or bit position.")
            return None, None
        return register_address, bit_position
