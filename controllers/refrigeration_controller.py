import time
from plc_writer import PLCWriter
from plc_reader import PLCReader
import logging


logger = logging.getLogger(__name__)


class RefrigerationSystemController:
    def __init__(self, plc_reader, plc_writer, plc_name="Main PLC"):
        """
        Initialize the refrigeration system controller.
        
        :param plc_reader: Instance of PLCReader for reading data points.
        :param plc_writer: Instance of PLCWriter for writing data points.
        :param plc_name: Name of the PLC to communicate with.
        """
        self.plc_reader = plc_reader
        self.plc_writer = plc_writer
        self.plc_name = plc_name

    def read_suction_pressure(self):
        """
        Reads the suction pressure from the PLC.
        :return: Suction pressure value or None if reading fails.
        """
        data_point = "COMP_1_SUC_PRESSURE"
        result = self.plc_reader.read_single_register(self.plc_name, self.plc_writer.clients[self.plc_name], {
            "address": 41226,  # Update with the correct address if necessary
            "type": "REAL",
            "description": "Suction Pressure of Compressor 1",
            "raw_zero_scale": 0,
            "raw_full_scale": 100,
            "eng_zero_scale": 0,
            "eng_full_scale": 100,
        })
        return result.get("scaled_value") if result else None

    def write_bit(self, signal_name, bit_position, value):
        """
        Writes a value to a specific bit of a signal.
        :param signal_name: Name of the signal (e.g., "COMP_1_WR").
        :param bit_position: Bit position to modify.
        :param value: Value to write (0 or 1).
        """
        signal_details = self.plc_writer.write_signals.get(signal_name)
        if not signal_details:
            logger.warning("Signal '%s' not found.", signal_name)
            return False
        register_address = signal_details["register"]
        return self.plc_writer.bit_write_signal(self.plc_name, register_address, bit_position, value)

    def start_condenser(self):
        """
        Starts the condenser by toggling the EVAP_COND_1_CTRL_STS bit 0.
        """
        self.write_bit("EVAP_COND_1_CTRL_STS", 0, 1)
        time.sleep(0.1)
        self.write_bit("EVAP_COND_1_CTRL_STS", 0, 0)

    def start_compressor(self):
        """
        Starts the compressor by toggling the COMP_1_WR bit 0.
        """
        self.write_bit("COMP_1_WR", 0, 1)
        time.sleep(0.1)
        self.write_bit("COMP_1_WR", 0, 0)

    def load_compressor(self):
        """
        Loads the compressor by setting COMP_1_WR bit 3.
        """
        self.write_bit("COMP_1_WR", 3, 1)

    def unload_compressor(self):
        """
        Unloads the compressor by setting COMP_1_WR bit 4.
        """
        self.write_bit("COMP_1_WR", 4, 1)

    def stop_compressor(self):
        """
        Stops the compressor by toggling the COMP_1_WR bit 1.
        """
        self.write_bit("COMP_1_WR", 1, 1)
        time.sleep(0.1)
        self.write_bit("COMP_1_WR", 1, 0)

    def stop_condenser(self):
        """
        Stops the condenser by toggling the EVAP_COND_1_CTRL_STS bit 0.
        """
        self.write_bit("EVAP_COND_1_CTRL_STS", 0, 1)
        time.sleep(0.1)
        self.write_bit("EVAP_COND_1_CTRL_STS", 0, 0)

    def control_suction_pressure(self):
        """
        Monitors and controls the suction pressure according to the algorithm.
        """
        while True:
            suction_pressure = self.read_suction_pressure()
            if suction_pressure is None:
                logger.warning("Failed to read suction pressure.")
                time.sleep(1)
                continue

            logger.info("Suction Pressure: %s", suction_pressure)

            if suction_pressure >= 50:
                logger.info("High suction pressure detected. Starting condenser, compressor, and loading compressor.")
                self.start_condenser()
                self.start_compressor()
                self.load_compressor()

            elif suction_pressure <= 35:
                logger.info("Low suction pressure detected. Unloading compressor, stopping compressor, and stopping condenser.")
                self.unload_compressor()
                self.stop_compressor()
                self.stop_condenser()

            time.sleep(1)  # Polling interval