"""
Polls the JeruFun API every POLL_INTERVAL_MINUTES and stores results in SQLite.
Run standalone:  python collector.py
"""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

IL_TZ = ZoneInfo("Asia/Jerusalem")

from config import MAP_URL, STATION_URL, BIKE_TYPES, POLL_INTERVAL_MINUTES
from db import init_db, insert_snapshots, upsert_station_address

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JeruFunMonitor/1.0"})


def fetch_map() -> list[dict]:
    resp = SESSION.get(MAP_URL, timeout=15)
    resp.raise_for_status()
    return resp.json().get("stations", [])


def fetch_station_detail(station_id: str) -> dict:
    resp = SESSION.get(f"{STATION_URL}/{station_id}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def collect_once() -> int:
    ts = datetime.now(IL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    stations = fetch_map()
    rows = []

    for s in stations:
        station_id = s["id"]
        regular = 0
        electric = 0
        docks_free = 0
        bikes_total = s.get("omniBikes", 0)

        # fetch detail only when there are bikes (saves API calls for empty stations)
        if bikes_total > 0 or s.get("status") == "Active":
            try:
                detail = fetch_station_detail(station_id)
                docks_free = detail.get("availableOmniPoles", 0)
                if addr := detail.get("address", ""):
                    upsert_station_address(station_id, addr)
                for b in detail.get("availableBikes", []):
                    kind = BIKE_TYPES.get(b["bikeType"], "unknown")
                    if kind == "regular":
                        regular += b["availableBikes"]
                    elif kind == "electric":
                        electric += b["availableBikes"]
            except Exception as e:
                log.warning("station %s detail failed: %s", s.get("name"), e)

        disabled = max(0, bikes_total - (regular + electric))

        rows.append({
            "ts": ts,
            "station_id": station_id,
            "station_name": s.get("name", ""),
            "status": s.get("status", ""),
            "bikes_regular": regular,
            "bikes_electric": electric,
            "bikes_total": bikes_total,
            "docks_free": docks_free,
            "bikes_disabled": disabled,
        })

    insert_snapshots(rows)
    log.info("Collected %d stations at %s", len(rows), ts)
    return len(rows)


def _try_regenerate():
    try:
        import generate_dashboard
        generate_dashboard.generate()
        log.info("Dashboard regenerated")
    except Exception as e:
        log.warning("Dashboard regeneration failed: %s", e)


def run_loop():
    init_db()
    log.info("Collector started — polling every %d minutes", POLL_INTERVAL_MINUTES)
    while True:
        try:
            collect_once()
            _try_regenerate()
        except Exception as e:
            log.error("Collection failed: %s", e)
        time.sleep(POLL_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="collect one snapshot and exit")
    args = parser.parse_args()
    if args.once:
        init_db()
        collect_once()
        _try_regenerate()
    else:
        run_loop()
