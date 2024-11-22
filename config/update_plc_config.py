import yaml

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

print(f"Generated {output_file} with {count} data points.")
