import json
import yaml

# Input and output file paths
input_file = "data.json"
output_file = "plc_points.yaml"

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

    print(f"Generated {output_file} with Modbus addresses returning non-zero values.")

except FileNotFoundError:
    print(f"Error: {input_file} does not exist.")
except ValueError as ve:
    print(f"Value Error: {ve}")
except json.JSONDecodeError as je:
    print(f"JSON Decode Error: {je}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
