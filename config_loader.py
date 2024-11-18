import yaml
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")

def load_config(config_type):
    """
    Load the configuration based on the specified type.

    :param config_type: The type of configuration ('comp', 'vfd', or 'hmi')
    :return: The loaded configuration as a dictionary
    """
    config_files = {
        "screws": os.path.join(CONFIG_DIR, "screw_comp_config.yaml"),
        "viltors": os.path.join(CONFIG_DIR, "viltor_comp_config.yaml"),
        "vfd": os.path.join(CONFIG_DIR, "vfd_config.yaml"),
        "hmi": os.path.join(CONFIG_DIR, "hmi_config.yaml"),
        "plc": os.path.join(CONFIG_DIR, "plc_config.yaml")
    }

    config_path = config_files.get(config_type)
    if not config_path or not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file for {config_type} not found.")

    with open(config_path, "r") as file:
        return yaml.safe_load(file)
