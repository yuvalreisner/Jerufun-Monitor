import sqlite3
import pandas as pd
from config import DB_PATH, BLACKLIST_STATIONS, SHABBAT_STATIONS

_BL = tuple(BLACKLIST_STATIONS)
_BL_SQL  = ",".join(f"'{s}'" for s in _BL)
_SHAB_SQL = ",".join(f"'{s}'" for s in SHABBAT_STATIONS)


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             DATETIME NOT NULL,
            station_id     TEXT NOT NULL,
            station_name   TEXT NOT NULL,
            status         TEXT,
            bikes_regular  INTEGER DEFAULT 0,
            bikes_electric INTEGER DEFAULT 0,
            bikes_total    INTEGER DEFAULT 0,
            docks_free     INTEGER DEFAULT 0,
            bikes_disabled INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ts      ON snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_station ON snapshots(station_id, ts);
        CREATE TABLE IF NOT EXISTS station_meta (
            station_id  TEXT PRIMARY KEY,
            address     TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()


def insert_snapshots(rows: list[dict]):
    conn = get_conn()
    conn.executemany("""
        INSERT INTO snapshots
            (ts, station_id, station_name, status,
             bikes_regular, bikes_electric, bikes_total, docks_free, bikes_disabled)
        VALUES
            (:ts, :station_id, :station_name, :status,
             :bikes_regular, :bikes_electric, :bikes_total, :docks_free, :bikes_disabled)
    """, rows)
    conn.commit()
    conn.close()


def get_latest_snapshot() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(f"""
        SELECT s.*
        FROM snapshots s
        INNER JOIN (
            SELECT station_id, MAX(ts) AS max_ts
            FROM snapshots
            GROUP BY station_id
        ) latest ON s.station_id = latest.station_id AND s.ts = latest.max_ts
        WHERE s.station_name NOT IN ({_BL_SQL})
        ORDER BY s.station_name
    """, conn)
    conn.close()
    return df


def get_shortage_leaderboard(hours: int) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(f"""
        SELECT
            station_name,
            COUNT(*) AS total_samples,
            SUM(CASE WHEN bikes_regular + bikes_electric = 0 THEN 1 ELSE 0 END) AS empty_samples,
            ROUND(100.0 * SUM(CASE WHEN bikes_regular + bikes_electric = 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_empty,
            ROUND(AVG(bikes_regular + bikes_electric), 1) AS avg_bikes,
            MAX(bikes_regular + bikes_electric) AS max_bikes
        FROM snapshots
        WHERE ts >= datetime('now', ?) AND station_name NOT IN ({_BL_SQL})
        GROUP BY station_id, station_name
        HAVING total_samples > 0
        ORDER BY pct_empty DESC, avg_bikes ASC
    """, conn, params=(f"-{hours} hours",))
    conn.close()
    df["max_empty_streak_h"] = df.apply(
        lambda r: round(r["empty_samples"] * 5 / 60, 1), axis=1
    )
    return df


def get_station_timeseries(station_name: str, hours: int) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT ts, bikes_regular, bikes_electric, bikes_total, docks_free, bikes_disabled
        FROM snapshots
        WHERE station_name = ? AND ts >= datetime('now', ?)
        ORDER BY ts
    """, conn, params=(station_name, f"-{hours} hours"))
    conn.close()
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
        df["bikes_available"] = df["bikes_regular"] + df["bikes_electric"]
    return df


def get_van_events(station_name: str, hours: int, jump: int = 3) -> pd.DataFrame:
    df = get_station_timeseries(station_name, hours)
    if df.empty or len(df) < 2:
        return pd.DataFrame()
    df["delta"] = df["bikes_available"].diff()
    return df[df["delta"] >= jump][["ts", "bikes_available", "delta"]].copy()


def get_hourly_heatmap(station_name: str, days: int) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT
            CAST(strftime('%w', ts) AS INTEGER) AS dow,
            CAST(strftime('%H', ts) AS INTEGER) AS hour,
            AVG(bikes_regular + bikes_electric) AS avg_bikes
        FROM snapshots
        WHERE station_name = ? AND ts >= datetime('now', ?)
        GROUP BY dow, hour
        ORDER BY dow, hour
    """, conn, params=(station_name, f"-{days} days"))
    conn.close()
    return df


def get_disabled_leaderboard(hours: int) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(f"""
        SELECT
            station_name,
            ROUND(AVG(bikes_disabled), 1) AS avg_disabled,
            MAX(bikes_disabled) AS max_disabled,
            ROUND(AVG(bikes_regular + bikes_electric), 1) AS avg_available
        FROM snapshots
        WHERE ts >= datetime('now', ?) AND station_name NOT IN ({_BL_SQL})
        GROUP BY station_id, station_name
        HAVING avg_disabled > 0
        ORDER BY avg_disabled DESC
    """, conn, params=(f"-{hours} hours",))
    conn.close()
    return df


def get_avg_bikes_histogram(hours: int) -> pd.DataFrame:
    """Returns avg bikes per station for histogram bucketing."""
    conn = get_conn()
    df = pd.read_sql_query(f"""
        SELECT
            station_name,
            ROUND(AVG(bikes_regular + bikes_electric), 2) AS avg_bikes
        FROM snapshots
        WHERE ts >= datetime('now', ?) AND station_name NOT IN ({_BL_SQL})
        GROUP BY station_id, station_name
        HAVING COUNT(*) > 0
    """, conn, params=(f"-{hours} hours",))
    conn.close()
    return df


def count_snapshots() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    conn.close()
    return n


def get_collection_range() -> tuple:
    conn = get_conn()
    row = conn.execute("SELECT MIN(ts), MAX(ts) FROM snapshots").fetchone()
    conn.close()
    return row[0], row[1]


def get_hourly_timeseries_all(hours: int) -> dict:
    """Returns hourly-averaged timeseries for every station as {name: [{ts, reg, elec, dis}]}."""
    conn = get_conn()
    df = pd.read_sql_query(f"""
        SELECT
            station_name,
            strftime('%Y-%m-%dT%H:00', ts) AS hour,
            ROUND(AVG(bikes_regular), 1)  AS reg,
            ROUND(AVG(bikes_electric), 1) AS elec,
            ROUND(AVG(bikes_disabled), 1) AS dis
        FROM snapshots
        WHERE ts >= datetime('now', ?) AND station_name NOT IN ({_BL_SQL})
        GROUP BY station_name, hour
        ORDER BY station_name, hour
    """, conn, params=(f"-{hours} hours",))
    conn.close()
    result = {}
    for name, grp in df.groupby("station_name"):
        result[name] = grp[["hour", "reg", "elec", "dis"]].to_dict(orient="records")
    return result


def get_daily_network_summary() -> pd.DataFrame:
    """One row per calendar day: network-level avg/median, empty counts, disabled total."""
    conn = get_conn()
    df_raw = pd.read_sql_query(f"""
        SELECT
            DATE(ts) AS date,
            station_name,
            AVG(bikes_regular + bikes_electric)           AS avg_avail,
            AVG(bikes_electric)                           AS avg_elec,
            AVG(bikes_regular)                            AS avg_reg,
            AVG(bikes_disabled)                           AS avg_dis,
            SUM(CASE WHEN bikes_regular+bikes_electric=0
                     THEN 1 ELSE 0 END)*1.0/COUNT(*)     AS frac_empty,
            SUM(CASE WHEN bikes_electric=0
                     THEN 1 ELSE 0 END)*1.0/COUNT(*)     AS frac_no_elec
        FROM snapshots
        WHERE station_name NOT IN ({_BL_SQL})
        GROUP BY DATE(ts), station_name
        ORDER BY DATE(ts)
    """, conn)
    conn.close()

    if df_raw.empty:
        return pd.DataFrame()

    rows = []
    for date, grp in df_raw.groupby("date"):
        rows.append({
            "date": date,
            "avg_available":      round(float(grp["avg_avail"].mean()), 2),
            "median_available":   round(float(grp["avg_avail"].median()), 2),
            "avg_electric":       round(float(grp["avg_elec"].mean()), 2),
            "avg_regular":        round(float(grp["avg_reg"].mean()), 2),
            "total_disabled":     round(float(grp["avg_dis"].sum()), 1),
            "empty_stations":     int((grp["frac_empty"]   > 0.5).sum()),
            "no_electric_stations": int((grp["frac_no_elec"] > 0.5).sum()),
        })
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def get_all_daily_station_summary() -> dict:
    """Returns {station_name: [{date, elec, reg, dis}]} — daily averages per station."""
    conn = get_conn()
    df = pd.read_sql_query(f"""
        SELECT
            DATE(ts)                   AS date,
            station_name,
            ROUND(AVG(bikes_electric), 2) AS elec,
            ROUND(AVG(bikes_regular),  2) AS reg,
            ROUND(AVG(bikes_disabled), 2) AS dis
        FROM snapshots
        WHERE station_name NOT IN ({_BL_SQL})
        GROUP BY DATE(ts), station_name
        ORDER BY station_name, DATE(ts)
    """, conn)
    conn.close()

    result = {}
    for name, grp in df.groupby("station_name"):
        result[name] = grp[["date", "elec", "reg", "dis"]].to_dict(orient="records")
    return result


def upsert_station_address(station_id: str, address: str):
    conn = get_conn()
    conn.execute("""
        INSERT INTO station_meta (station_id, address) VALUES (?, ?)
        ON CONFLICT(station_id) DO UPDATE SET address = excluded.address
    """, (station_id, address))
    conn.commit()
    conn.close()


def get_all_station_addresses() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT station_id, address FROM station_meta").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def get_daily_rides() -> pd.DataFrame:
    """
    Counts observed rides per day: negative bike deltas between consecutive
    snapshots (<=2h gap, <=5 bikes/station/snapshot — maintenance filter).
    This is a lower bound; rides that start and end between two snapshots
    at the same station are not captured.
    """
    conn = get_conn()
    df = pd.read_sql_query(f"""
        WITH ordered AS (
            SELECT station_name, ts, bikes_electric, bikes_regular,
                   LAG(bikes_electric) OVER (PARTITION BY station_name ORDER BY ts) AS prev_elec,
                   LAG(bikes_regular)  OVER (PARTITION BY station_name ORDER BY ts) AS prev_reg,
                   LAG(ts)             OVER (PARTITION BY station_name ORDER BY ts) AS prev_ts
            FROM snapshots
            WHERE station_name NOT IN ({_BL_SQL})
        ),
        deltas AS (
            SELECT ts,
                -- count each type independently; rides = elec + reg always
                CASE WHEN (julianday(ts)-julianday(prev_ts))*24 <= 2
                          AND bikes_electric < prev_elec
                          AND (prev_elec - bikes_electric) <= 5
                          AND (
                               station_name IN ({_SHAB_SQL})
                               OR (
                                 STRFTIME('%w', ts) != '6'
                                 AND NOT (STRFTIME('%w', ts) = '5'
                                          AND TIME(ts) >= '19:05')
                               )
                             )
                     THEN prev_elec - bikes_electric ELSE 0 END AS taken_elec,
                CASE WHEN (julianday(ts)-julianday(prev_ts))*24 <= 2
                          AND bikes_regular < prev_reg
                          AND (prev_reg - bikes_regular) <= 5
                     THEN prev_reg - bikes_regular ELSE 0 END AS taken_reg
            FROM ordered WHERE prev_ts IS NOT NULL
        )
        SELECT STRFTIME('%Y-%m-%dT%H:00', ts) AS hour,
               SUM(taken_elec + taken_reg) AS rides,
               SUM(taken_elec) AS elec,
               SUM(taken_reg)  AS reg
        FROM deltas
        GROUP BY hour
        ORDER BY hour
    """, conn)
    conn.close()
    return df


def get_all_station_names() -> list[str]:
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT DISTINCT station_name FROM snapshots
        WHERE station_name NOT IN ({_BL_SQL})
        ORDER BY station_name
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]
