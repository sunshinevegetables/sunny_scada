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
        logger.info("########################")
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
