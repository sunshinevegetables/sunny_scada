from flask import Flask, send_from_directory, jsonify
import yaml
import os

app = Flask(__name__, static_folder="static")

# Load processes from the YAML file
def load_processes():
    config_path = "config/processes.yaml"
    if os.path.exists(config_path):
        with open(config_path, "r") as file:
            data = yaml.safe_load(file)
            return data.get("processes", [])
    return []

processes = load_processes()

@app.route('/')
def index():
    """
    Serve the main HTML page.
    """
    return send_from_directory("static/pages", "index.html")

@app.route('/processes', methods=['GET'])
def list_processes():
    """
    Return the list of processes as JSON.
    """
    return jsonify(processes)

# Serve static files
@app.route('/scripts/<path:filename>')
def serve_scripts(filename):
    """
    Serve JavaScript files.
    """
    return send_from_directory("static/scripts", filename)

@app.route('/styles/<path:filename>')
def serve_styles(filename):
    """
    Serve CSS files.
    """
    return send_from_directory("static/styles", filename)

@app.route('/images/<path:filename>')
def serve_images(filename):
    """
    Serve image files.
    """
    return send_from_directory("static/images", filename)

@app.route('/sounds/<path:filename>')
def serve_sounds(filename):
    """
    Serve audio files.
    """
    return send_from_directory("static/sounds", filename)

if __name__ == "__main__":
    app.run(debug=True)
