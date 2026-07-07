import os

# Defaults match the Docker image's bind-mount points (see compose.yml). Overridable so the
# app can run natively (e.g. under a debugger) against local folders instead of the container root.
DATA_DIR = os.getenv('DATA_DIR', '/data')
CONFIG_DIR = os.getenv('CONFIG_DIR', '/config')
TEMP_DIR = os.getenv('TEMP_DIR', '/temp')
