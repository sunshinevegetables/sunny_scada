import pyttsx3
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Text to be converted into speech
text = "ALERT! SUCTION PRESSURE IS HIGHER THAN THRESHOLD. IT IS SUGGESTED TO TURN ON THE COMPRESSOR TO BRING DOWN THE SUCTION PRESSURE."

# Initialize the pyttsx3 engine
engine = pyttsx3.init()

# Set properties (optional)
engine.setProperty('rate', 150)  # Speed of speech
engine.setProperty('volume', 1)  # Volume (0.0 to 1.0)

# Save the speech to a file
filename = "high_suction_pressure_alarm.wav"
engine.save_to_file(text, filename)

# Process the speech synthesis
engine.runAndWait()

logger.info("Alarm sound saved as %s", filename)