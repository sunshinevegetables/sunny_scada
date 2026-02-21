import yaml
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Define start address and number of points
start_address = 40001
count = 999

# Generate the data points dictionary
data_points = {f"MODBUS ADDRESS {start_address + i}": start_address + i for i in range(count)}

# Save to YAML file with explicit quoting for keys
output_file = "viltor_points.yaml"
with open(output_file, "w") as file:
    yaml.dump(
        {"data_points": data_points}, 
        file, 
        default_flow_style=False, 
        allow_unicode=True, 
        sort_keys=False, 
        Dumper=yaml.Dumper
    )

logger.info("Generated %s with %s data points.", output_file, count)
