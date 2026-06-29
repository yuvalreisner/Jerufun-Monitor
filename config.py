import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE = "https://api.fsmctmobility.com/api/mobile/jerufun/v1"
MAP_URL = f"{API_BASE}/map"
STATION_URL = f"{API_BASE}/station"

DB_PATH = os.path.join(BASE_DIR, "jerufun.db")

POLL_INTERVAL_MINUTES = 60

BIKE_TYPES = {
    "5fd79b3b490b8b798b72da4e": "electric",
    "60211520055d7be6788f83a0": "regular",
    "68b05b20905eda42fc20db9a": "scooter",
}

# Thresholds for color coding
LOW_BIKES_THRESHOLD = 2   # yellow
ZERO_BIKES_THRESHOLD = 0  # red
VAN_JUMP_THRESHOLD = 3    # min bike increase to flag as van arrival

BLACKLIST_STATIONS = {"BACKYARD TEST", 'ת"א - טסט'}
