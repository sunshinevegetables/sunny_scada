from plc_writer import PLCWriter

class Commands:
    def __init__(self, plc_writer):
        """
        Initialize with a PLCWriter instance.

        :param plc_writer: Instance of PLCWriter to handle Modbus writes.
        """
        self.plc_writer = plc_writer

    def compressor_start(self, plc_name):
        """
        Start the compressor for the specified PLC.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "COMPRESSOR START", 1)

    def compressor_stop(self, plc_name):
        """
        Stop the compressor for the specified PLC.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "COMPRESSOR STOP", 1)

    def slide_valve_manual_auto(self, plc_name, value):
        """
        Set the slide valve to manual or auto mode.

        :param plc_name: Name of the PLC.
        :param value: 1 for manual mode, 0 for auto mode.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "SLIDE VALVE MANUAL/AUTO PB", value)

    def load_pb(self, plc_name):
        """
        Load PB for the specified PLC.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "LOAD PB", 1)

    def unload_pb(self, plc_name):
        """
        Unload PB for the specified PLC.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "UNLOAD PB", 1)

    def pump_selector_switch_man_mode(self, plc_name):
        """
        Set pump selector switch to manual mode.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "PUMP SELECTOR SWITCH MAN MODE", 1)

    def accept_pb(self, plc_name):
        """
        Accept PB for the specified PLC.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "ACCEPT PB", 1)

    def reset_pb(self, plc_name):
        """
        Reset PB for the specified PLC.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "RESET PB", 1)

    def modbus_mode(self, plc_name, value):
        """
        Set Modbus mode for the specified PLC.

        :param plc_name: Name of the PLC.
        :param value: 1 to enable Modbus mode, 0 to disable.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "MODBUS MODE", value)

    def pump_selector_switch_auto_mode(self, plc_name):
        """
        Set pump selector switch to auto mode.

        :param plc_name: Name of the PLC.
        :return: Result of the write operation.
        """
        return self.plc_writer.write_signal(plc_name, "PUMP SELECTOR SWITCH AUTO MODE", 1)
