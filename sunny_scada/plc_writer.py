from pymodbus.client import ModbusTcpClient
import yaml
import logging
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PLCWriter:
    def __init__(self, config_type):
        """
        Initialize PLCWriter with a configuration type to load the relevant YAML configuration.

        :param config_type: The type of configuration ('comps', 'vfd', etc.)
        """
        try:
            # Load the write signals from the respective YAML file
            write_points_path = os.path.join("config", f"{config_type}_write_points.yaml")
            if not os.path.exists(write_points_path):
                raise FileNotFoundError(f"Write points file not found: {write_points_path}")

            with open(write_points_path, "r") as file:
                self.write_signals = yaml.safe_load(file).get("data_points", {})
            
            if not self.write_signals:
                raise ValueError(f"No data points found in {write_points_path}")

            # Load PLC configurations dynamically
            config_path = os.path.join("config", f"{config_type}_config.yaml")
            if not os.path.exists(config_path):
                raise FileNotFoundError(f"PLC configuration file not found: {config_path}")

            with open(config_path, "r") as file:
                plc_config = yaml.safe_load(file).get(config_type, [])

            if not plc_config:
                raise ValueError(f"No PLC configurations found in {config_path}")

            # Initialize Modbus clients for each PLC
            self.clients = {
                plc["name"]: ModbusTcpClient(plc["ip"], port=plc["port"]) for plc in plc_config
            }

            logger.info(f"Initialized PLCWriter for '{config_type}' with {len(self.clients)} clients.")

        except FileNotFoundError as e:
            logger.error(e)
            raise
        except Exception as e:
            logger.error(f"Error initializing PLCWriter: {e}")
            raise

    def write_signal(self, plc_name, signal_name, value):
        """
        Write a value to a specific signal on the PLC.

        :param plc_name: Name of the PLC as specified in the configuration.
        :param signal_name: Name of the signal to write (e.g., "COMPRESSOR START").
        :param value: Value to write (e.g., 1 for ON, 0 for OFF).
        :return: True if successful, False otherwise.
        """
        logger.info("##### Write Signal #####")
        logger.info(f"PLC Name: {plc_name}")
        logger.info(f"Signal Name: {signal_name}")
        logger.info(f"Value: {value}")
        logger.info(f" {self.clients}")
        if plc_name not in self.clients:
            logger.error(f"PLC '{plc_name}' not found in configuration.")
            return False

        if signal_name not in self.write_signals:
            logger.error(f"Signal '{signal_name}' not found in write mapping.")
            return False

        modbus_address = self.write_signals[signal_name] - 40001  # Adjust for pymodbus 0-based offset

        client = self.clients[plc_name]
        try:
            if not client.connect():
                logger.error(f"Failed to connect to PLC '{plc_name}'")
                return False

            logger.info(f"Writing {value} to {signal_name} at Modbus address {modbus_address} on {plc_name}")

            # Attempt to write as a coil
            response = client.write_coil(modbus_address, value)
            if response.isError():
                logger.warning(f"Coil write failed for {signal_name}. Trying register write...")
                # Attempt to write as a register
                response = client.write_register(modbus_address, value)
                if response.isError():
                    logger.error(f"Register write failed for {signal_name} at address {modbus_address}")
                    return False
                else:
                    logger.info(f"Successfully wrote {value} to {signal_name} as register.")
            else:
                logger.info(f"Successfully wrote {value} to {signal_name} as coil.")
            return True

        except Exception as e:
            logger.error(f"Exception while writing to {signal_name} on {plc_name}: {e}")
            return False
        finally:
            client.close()

    def viltor_write_signal(self, plc_name, signal_name, value):
        """
        Write a bit to a specific position within a Modbus register for Viltor compressors.

        :param plc_name: Name of the PLC as specified in the configuration.
        :param signal_name: Name of the signal to write (e.g., "MOD_START").
        :param value: Value to write (1 for ON, 0 for OFF).
        :return: True if successful, False otherwise.
        """
        logger.info("##### Viltor Write Signal #####")
        logger.info(f"PLC Name: {plc_name}")
        logger.info(f"Signal Name: {signal_name}")
        logger.info(f"Value: {value}")

        if plc_name not in self.clients:
            logger.error(f"PLC '{plc_name}' not found in configuration.")
            return False

        if signal_name not in self.write_signals:
            logger.error(f"Signal '{signal_name}' not found in write mapping.")
            return False

        # Get the register address and bit position from the write_signals mapping
        write_info = self.write_signals[signal_name]
        register_address = write_info.get("register") - 40001  # Adjust for pymodbus 0-based indexing
        bit_position = write_info.get("bit")

        if register_address is None or bit_position is None:
            logger.error(f"Invalid signal mapping for '{signal_name}': Missing register or bit position.")
            return False

        client = self.clients[plc_name]
        try:
            if not client.connect():
                logger.error(f"Failed to connect to PLC '{plc_name}'")
                return False

            logger.info(f"Writing {value} to {signal_name} at register {register_address}, bit {bit_position} on {plc_name}")

            # Read the current value of the register
            response = client.read_holding_registers(register_address, 1)
            if response.isError():
                logger.error(f"Failed to read register {register_address} from PLC '{plc_name}'.")
                return False

            # Perform bitwise operation to modify the desired bit
            current_value = response.registers[0]
            if value == 1:
                new_value = current_value | (1 << bit_position)  # Set the bit
            else:
                new_value = current_value & ~(1 << bit_position)  # Clear the bit

            # Write the modified value back to the register
            write_response = client.write_register(register_address, new_value)
            if write_response.isError():
                logger.error(f"Failed to write modified value {new_value} to register {register_address}.")
                return False

            logger.info(f"Successfully wrote bit {bit_position} with value {value} to register {register_address}.")
            return True
        except Exception as e:
            logger.error(f"Exception during viltor_write_signal for '{signal_name}' on '{plc_name}': {e}")
            return False
        finally:
            client.close()

    def plc_write_signal(self, plc_name, signal_name, value):
        """
        Write a bit to a specific position within a Modbus register or directly to a register for a generic PLC.

        :param plc_name: Name of the PLC as specified in the configuration.
        :param signal_name: Name of the signal to write (e.g., "START").
        :param value: Value to write (1 for ON, 0 for OFF, or a numeric value for direct register write).
        :return: True if successful, False otherwise.
        """
        logger.info("##### PLC Write Signal #####")
        logger.info(f"PLC Name: {plc_name}")
        logger.info(f"Signal Name: {signal_name}")
        logger.info(f"Value: {value}")

        if plc_name not in self.clients:
            logger.error(f"PLC '{plc_name}' not found in configuration.")
            return False

        if signal_name not in self.write_signals:
            logger.error(f"Signal '{signal_name}' not found in write mapping.")
            return False

        # Get the register address and bit position from the write_signals mapping
        write_info = self.write_signals[signal_name]
        register_address = write_info.get("register") - 40001  # Adjust for pymodbus 0-based indexing
        bit_position = write_info.get("bit")

        # Determine if this is a bitwise operation or a direct register write
        is_bitwise = bit_position is not None

        client = self.clients[plc_name]
        try:
            if not client.connect():
                logger.error(f"Failed to connect to PLC '{plc_name}'")
                return False

            if is_bitwise:
                logger.info(f"Performing bitwise write: {value} to {signal_name} at register {register_address}, bit {bit_position}")

                # Read the current value of the register
                response = client.read_holding_registers(register_address, 1)
                if response.isError():
                    logger.error(f"Failed to read register {register_address} from PLC '{plc_name}'.")
                    return False

                # Perform bitwise operation to modify the desired bit
                current_value = response.registers[0]
                if value == 1:
                    new_value = current_value | (1 << bit_position)  # Set the bit
                else:
                    new_value = current_value & ~(1 << bit_position)  # Clear the bit

                # Write the modified value back to the register
                write_response = client.write_register(register_address, new_value)
                if write_response.isError():
                    logger.error(f"Failed to write modified value {new_value} to register {register_address}.")
                    return False

                logger.info(f"Successfully wrote bit {bit_position} with value {value} to register {register_address}.")
            else:
                logger.info(f"Performing direct register write: {value} to {signal_name} at register {register_address}")

                # Write the value directly to the register
                write_response = client.write_register(register_address, value)
                if write_response.isError():
                    logger.error(f"Failed to write value {value} to register {register_address}.")
                    return False

                logger.info(f"Successfully wrote value {value} to register {register_address}.")

            return True
        except Exception as e:
            logger.error(f"Exception during plc_write_signal for '{signal_name}' on '{plc_name}': {e}")
            return False
        finally:
            client.close()
