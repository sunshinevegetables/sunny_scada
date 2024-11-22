import struct

# Registers read from the PLC
high_word = 16934  # %MW1228
low_word = 26214   # %MW1229

# Combine the two registers into a 32-bit integer
combined_value = (high_word << 16) | low_word

# Decode as IEEE 754 floating-point (big-endian)
decoded_temp = struct.unpack('>f', combined_value.to_bytes(4, byteorder='big'))[0]

print(f"Decoded Temperature: {decoded_temp:.2f} °C")
