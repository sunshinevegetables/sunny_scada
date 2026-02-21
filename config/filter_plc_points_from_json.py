import json
import yaml
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Input and output file paths
input_file = "data.json"
output_file = "viltor_points.yaml"

try:
    # Load JSON data from the file
    with open(input_file, "r") as file:
        content = file.read()
        if not content.strip():
            raise ValueError("The JSON file is empty.")
        data = json.loads(content)

    # Assuming the structure is: {"Main PLC": {"data": {...}}}
    main_plc_data = data.get("Main PLC", {}).get("data", {})
    if not main_plc_data:
        raise ValueError("No valid 'data' section found in the JSON.")

    # Filter and transform data to include only non-zero values
    filtered_data = {key: int(key.split()[-1]) for key, value in main_plc_data.items() if value != 0}

    # Save filtered addresses to YAML file
    with open(output_file, "w") as file:
        yaml.dump({"data_points": filtered_data}, file, default_flow_style=False)

    logger.info("Generated %s with Modbus addresses returning non-zero values.", output_file)

except FileNotFoundError:
    logger.error("Error: %s does not exist.", input_file)
except ValueError as ve:
    logger.error("Value Error: %s", ve)
except json.JSONDecodeError as je:
    logger.error("JSON Decode Error: %s", je)
except Exception as e:
    logger.exception("An unexpected error occurred: %s", e)
