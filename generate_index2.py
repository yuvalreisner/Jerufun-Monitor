#!/usr/bin/env python3
"""
Generates index2.html from jerufun-redesign-proposal.html
populated with real data from jerufun.db.
"""
import os
import re
import json
import sqlite3
import urllib.request
import base64
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── DB helpers (inline to avoid import issues) ──────────────────────────────
import sys
sys.path.insert(0, BASE_DIR)

try:
    import pandas as pd
    from db import (
        get_latest_snapshot, get_daily_rides, get_shortage_leaderboard,
        get_daily_network_summary
    )
    from config import DB_PATH, BLACKLIST_STATIONS, SHABBAT_STATIONS
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    print("pandas not found — activate venv first")
    sys.exit(1)


# ── Utilities ────────────────────────────────────────────────────────────────
_BL_SQL = ','.join(f"'{s}'" for s in BLACKLIST_STATIONS)
_SHAB_SET = set(SHABBAT_STATIONS)

def fmt_il_date(ts_str):
    """'2026-07-12 13:22' → '13:22 12/7'"""
    try:
        dt = datetime.fromisoformat(ts_str)
        return f"{dt.hour:02d}:{dt.minute:02d} {dt.day}/{dt.month}"
    except Exception:
        return ts_str

def hebrew_date(date_str):
    """'2026-07-05' → '5/7'"""
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return f"{d.day}/{d.month}"
    except Exception:
        return date_str

def is_sunday(date_str):
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return d.weekday() == 6  # Sunday in Python is 6
    except Exception:
        return False


# ── Pull data ────────────────────────────────────────────────────────────────
snap = get_latest_snapshot()
snap['bikes_available'] = snap['bikes_regular'] + snap['bikes_electric']
snap = snap.sort_values('bikes_available', ascending=False)

total_available  = int(snap['bikes_available'].sum())
total_electric   = int(snap['bikes_electric'].sum())
total_regular    = int(snap['bikes_regular'].sum())
total_disabled   = int(snap['bikes_disabled'].sum())
total_fleet      = total_available + total_disabled
active_stations  = len(snap)
empty_stations   = int((snap['bikes_available'] == 0).sum())
elec_pct         = round(total_electric / total_available * 100) if total_available > 0 else 0
reg_pct          = 100 - elec_pct
avail_pct        = round(total_available / total_fleet * 100) if total_fleet > 0 else 0
disabled_pct     = round(total_disabled / total_fleet * 100) if total_fleet > 0 else 0

# Last update time
last_ts = snap['ts'].max() if 'ts' in snap.columns else ''
last_update = fmt_il_date(last_ts)

# ── Week-over-week comparison ─────────────────────────────────────────────────
conn_cmp = sqlite3.connect(DB_PATH)
# Find snapshot closest to same hour, 7 days ago (±3h window)
last_dt = datetime.fromisoformat(last_ts)
week_ago = last_dt - timedelta(days=7)
week_from = (week_ago - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
week_to   = (week_ago + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')

wow_row = conn_cmp.execute(f"""
    SELECT AVG(bikes_regular + bikes_electric) * COUNT(DISTINCT station_name) AS avail_total,
           AVG(bikes_disabled) * COUNT(DISTINCT station_name) AS disabled_total,
           SUM(CASE WHEN bikes_regular + bikes_electric = 0 THEN 1 ELSE 0 END) AS empty_count
    FROM snapshots
    WHERE ts BETWEEN '{week_from}' AND '{week_to}'
      AND station_name NOT IN ({_BL_SQL})
""").fetchone()
conn_cmp.close()

wow_avail    = round(float(wow_row[0] or 0))
wow_disabled = round(float(wow_row[1] or 0))
wow_empty    = int(wow_row[2] or 0)
wow_fleet    = wow_avail + wow_disabled
wow_avail_pct    = round(wow_avail / wow_fleet * 100) if wow_fleet > 0 else 0
wow_disabled_pct = round(wow_disabled / wow_fleet * 100) if wow_fleet > 0 else 0

avail_delta  = round(avail_pct - wow_avail_pct)
disabled_delta = round(disabled_pct - wow_disabled_pct)
empty_delta  = empty_stations - wow_empty

# Daily rides (all time)
rides_raw = get_daily_rides()
rides_raw['date'] = rides_raw['hour'].str[:10]
daily_rides = (rides_raw.groupby('date')[['rides','elec','reg']]
               .sum().reset_index().sort_values('date'))

# Hourly rides — last 48 hours, using same ride-counting logic
import datetime as _dt
_now = _dt.datetime.utcnow()
_cutoff = (_now - _dt.timedelta(hours=48)).strftime('%Y-%m-%dT%H:00')
hourly_rides_df = rides_raw[rides_raw['hour'] >= _cutoff].copy().sort_values('hour')
rhr_labels = [h[11:13] for h in hourly_rides_df['hour']]  # "HH" — overridden below after hourly_snap
rhr_a = [int(v) for v in hourly_rides_df['reg']]
rhr_b = [int(v) for v in hourly_rides_df['elec']]

# Last 30 days
recent_rides = daily_rides.tail(30).copy()
if recent_rides.empty:
    recent_rides = pd.DataFrame({'date':[],'rides':[],'elec':[],'reg':[]})

today_rides = int(daily_rides[daily_rides['date'] == daily_rides['date'].max()]['rides'].sum()) \
              if not daily_rides.empty else 0

# Shortage leaderboard (7 days)
shortage = get_shortage_leaderboard(hours=168)

# Weekly rides per station (custom query)
conn = sqlite3.connect(DB_PATH)
_SHAB_SQL = ','.join(f"'{s}'" for s in SHABBAT_STATIONS)
weekly_rides_df = pd.read_sql_query(f"""
    WITH ordered AS (
        SELECT station_name, ts, bikes_electric, bikes_regular,
               LAG(bikes_electric) OVER (PARTITION BY station_name ORDER BY ts) AS prev_elec,
               LAG(bikes_regular)  OVER (PARTITION BY station_name ORDER BY ts) AS prev_reg,
               LAG(ts)             OVER (PARTITION BY station_name ORDER BY ts) AS prev_ts
        FROM snapshots
        WHERE station_name NOT IN ({_BL_SQL})
          AND ts >= datetime('now', '-7 days')
    )
    SELECT station_name,
        SUM(CASE WHEN (julianday(ts)-julianday(prev_ts))*24 <= 2
                      AND bikes_electric < prev_elec AND (prev_elec - bikes_electric) <= 5
                 THEN prev_elec - bikes_electric ELSE 0 END) AS weekly_elec,
        SUM(CASE WHEN (julianday(ts)-julianday(prev_ts))*24 <= 2
                      AND bikes_regular < prev_reg  AND (prev_reg  - bikes_regular)  <= 5
                 THEN prev_reg  - bikes_regular  ELSE 0 END) AS weekly_reg
    FROM ordered WHERE prev_ts IS NOT NULL
    GROUP BY station_name
""", conn)
conn.close()
weekly_rides_df['weekly_total'] = weekly_rides_df['weekly_elec'] + weekly_rides_df['weekly_reg']

# Network summary for avg/median chart
net = get_daily_network_summary()


# ── Fetch real coordinates from API ──────────────────────────────────────────
import requests

try:
    api_resp = requests.get(
        'https://api.fsmctmobility.com/api/mobile/jerufun/v1/map', timeout=10)
    api_stations = api_resp.json().get('stations', [])
    coord_map = {s['name']: s['location'] for s in api_stations if 'location' in s}
except Exception as e:
    print(f"API unavailable: {e}. Using fallback positions.")
    coord_map = {}

# ── Load station addresses from DB ───────────────────────────────────────────
_conn_addr = sqlite3.connect(DB_PATH)
_addr_rows = _conn_addr.execute(
    "SELECT station_id, address FROM station_meta WHERE address != ''"
).fetchall()
_conn_addr.close()
addr_map = {r[0]: r[1] for r in _addr_rows}  # station_id → address

# Jerusalem bounding box → SVG 600×440 canvas (with padding)
LAT_MAX, LAT_MIN = 31.840, 31.695
LON_MIN, LON_MAX = 35.160, 35.280
SVG_W, SVG_H   = 600, 440
PAD_X, PAD_Y   = 20, 10

def latlon_to_xy(lat, lng):
    x = PAD_X + round((lng - LON_MIN) / (LON_MAX - LON_MIN) * SVG_W)
    y = PAD_Y + round((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * SVG_H)
    return x, y

import hashlib

def fallback_xy(name):
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    x = PAD_X + ((h >> 16) & 0xFFFF) % SVG_W
    y = PAD_Y + ((h & 0xFFFF)) % SVG_H
    return x, y

# ── Build Google Maps stations_geo array ─────────────────────────────────────
stations_geo = []
for _, row in snap.iterrows():
    name = row['station_name']
    loc  = coord_map.get(name, {})
    stations_geo.append({
        'name':      name,
        'address':   addr_map.get(str(row.get('station_id', '')), ''),
        'lat':       loc.get('lat'),
        'lng':       loc.get('lng'),
        'available': int(row['bikes_available']),
        'electric':  int(row.get('bikes_electric', 0)),
        'regular':   int(row.get('bikes_regular', 0)),
        'disabled':  int(row.get('bikes_disabled', 0)),
        'docks':     int(row.get('docks_free', 0)),
    })

stations_js = 'var stations = ' + json.dumps(stations_geo, ensure_ascii=False) + ';'


# ── Ranking table rows ────────────────────────────────────────────────────────
wr = weekly_rides_df.set_index('station_name').to_dict(orient='index')
max_bikes = max(1, snap['bikes_available'].max())

ranking_rows = []
for _, row in snap.iterrows():
    name = row['station_name']
    avail = int(row['bikes_available'])
    elec  = int(row['bikes_electric'])
    reg   = int(row['bikes_regular'])
    winfo = wr.get(name, {'weekly_total': 0, 'weekly_elec': 0, 'weekly_reg': 0})
    w_total = int(winfo['weekly_total'])
    w_elec  = int(winfo['weekly_elec'])
    w_reg   = int(winfo['weekly_reg'])
    reg_w  = round(reg / avail * 100, 2) if avail > 0 else 0
    elec_w = round(elec / avail * 100, 2) if avail > 0 else 0
    MAX_RIDES = 30
    w_reg_w  = round(w_reg  / MAX_RIDES * 100, 1)
    w_elec_w = round(w_elec / MAX_RIDES * 100, 1)
    ranking_rows.append(
        f'<tr data-bikes="{avail}" data-rides="{w_total}">'
        f'<td class="station-name">{name}</td>'
        f'<td><div class="bar-cell bar-hover2" data-total="{avail}" data-regular="{reg}" data-electric="{elec}" '
        f'data-total-label="סה&quot;כ אופניים" data-label="{name} · כמות אופניים">'
        f'<span class="bar-num" data-total="{avail}" data-regular="{reg}" data-electric="{elec}">{avail}</span>'
        f'<div class="bar-track">'
        f'<div class="seg regular" style="width:{reg_w}%;"></div>'
        f'<div class="seg electric" style="width:{elec_w}%;"></div>'
        f'</div></div></td>'
        f'<td><div class="bar-cell bar-hover2" data-total="{w_total}" data-regular="{w_reg}" data-electric="{w_elec}" '
        f'data-total-label="סה&quot;כ נסיעות שבועיות" data-label="{name} · נסיעות">'
        f'<span class="bar-num" data-total="{w_total}" data-regular="{w_reg}" data-electric="{w_elec}">{w_total}</span>'
        f'<div class="bar-track">'
        f'<div class="seg regular" style="width:{w_reg_w}%;"></div>'
        f'<div class="seg electric" style="width:{w_elec_w}%;"></div>'
        f'</div></div></td>'
        f'</tr>'
    )
ranking_tbody = '\n'.join(ranking_rows)


# ── Rides skyline bars ────────────────────────────────────────────────────────
max_day_rides = max(1, int(recent_rides['rides'].max()))
skyline_cols = []
skyline_labels = []
n_days = len(recent_rides)

for idx, (_, row) in enumerate(recent_rides.iterrows()):
    total = int(row['rides'])
    elec  = int(row['elec'])
    reg   = int(row['reg'])
    date_lbl = hebrew_date(row['date'])
    h_pct = round(total / max_day_rides * 100)
    reg_h  = round(reg  / total * 100) if total > 0 else 0
    elec_h = round(elec / total * 100) if total > 0 else 0
    skyline_cols.append(
        f'<div class="col bar-hover" style="height:{h_pct}%;" '
        f'data-date="{date_lbl}" data-regular="{reg}" data-electric="{elec}" data-total="{total}">'
        f'<span class="bar-value">{total}</span>'
        f'<div class="bar-fill">'
        f'<div class="seg regular" style="height:{reg_h}%;"></div>'
        f'<div class="seg electric" style="height:{elec_h}%;"></div>'
        f'</div></div>'
    )
    # Sunday label (highlight)
    is_sun = is_sunday(row['date'])
    style = ' style="color:var(--gold);font-weight:700;"' if is_sun else ''
    skyline_labels.append(f'<span{style}>{date_lbl}</span>')

# Add week markers for Sunday columns
week_markers = []
for idx, (_, row) in enumerate(recent_rides.iterrows()):
    if is_sunday(row['date']) and n_days > 1:
        left_pct = round(idx / (n_days - 1) * 100, 2) if n_days > 1 else 0
        week_markers.append(f'<div class="week-marker" style="left:{left_pct}%;"></div>')
        week_markers.append(f'<div class="wm-label" style="left:{left_pct}%;">א׳</div>')

rides_skyline_html = '\n'.join(skyline_cols + week_markers)
rides_labels_html  = '\n'.join(skyline_labels)


# ── Chronic empty cards ───────────────────────────────────────────────────────
chronic = shortage[shortage['pct_empty'] >= 50].head(9)
chronic_cards = []
for _, row in chronic.iterrows():
    name = row['station_name']
    streak_h = round(float(row.get('max_empty_streak_h', row['empty_samples'] * 0.25)), 1)
    pct = round(float(row['pct_empty']), 0)
    avg = round(float(row['avg_bikes']), 1)
    chronic_cards.append(
        f'<div class="chronic-card">'
        f'<div class="top"><span class="name">{name}</span>'
        f'<span><span class="streak num">{pct:.0f}%</span> <span style="font-size:11px;color:var(--text-faint);font-weight:500;">מהזמן ריקה</span></span></div>'
        f'<div class="meta">ממוצע {avg} אופניים · שבוע אחרון</div>'
        f'</div>'
    )
chronic_grid_html = '\n'.join(chronic_cards) if chronic_cards else '<p style="color:var(--text-faint);font-size:13px;">אין תחנות כרוניות ריקות 🎉</p>'


# ── Distribution histogram ────────────────────────────────────────────────────
buckets = [
    ('0', snap['bikes_available'] == 0),
    ('1-2', (snap['bikes_available'] >= 1) & (snap['bikes_available'] <= 2)),
    ('3-4', (snap['bikes_available'] >= 3) & (snap['bikes_available'] <= 4)),
    ('5-6', (snap['bikes_available'] >= 5) & (snap['bikes_available'] <= 6)),
    ('7-8', (snap['bikes_available'] >= 7) & (snap['bikes_available'] <= 8)),
    ('9-10',(snap['bikes_available'] >= 9) & (snap['bikes_available'] <= 10)),
    ('11+', snap['bikes_available'] >= 11),
]
counts = [(lbl, int(mask.sum())) for lbl, mask in buckets]
max_count = max(1, max(c for _, c in counts))
total_st = len(snap)

hist_bars = []
for lbl, cnt in counts:
    h_pct = round(cnt / max_count * 100, 1)
    pct_st = round(cnt / total_st * 100, 1)
    hist_bars.append(
        f'<div class="bar bar-hover" style="height:{h_pct}%;" '
        f'data-date="{lbl} אופניים" data-total-raw="{cnt} תחנות · {pct_st}% מכלל התחנות">'
        f'<span class="hist-value"><span class="n">{cnt}</span><span class="p">{pct_st}%</span></span>'
        f'</div>'
    )
hist_html = '\n'.join(hist_bars)


# ── DATA object for JS charts ────────────────────────────────────────────────
def make_labels_and_sundays(dates):
    labels = [hebrew_date(d) for d in dates]
    sundays = [i for i, d in enumerate(dates) if is_sunday(d)]
    return labels, sundays

# Daily rides DATA
dr_dates = list(recent_rides['date'])
dr_labels, dr_sundays = make_labels_and_sundays(dr_dates)
dr_a = [int(v) for v in recent_rides['reg']]
dr_b = [int(v) for v in recent_rides['elec']]

# Hourly snapshots — last 48 hours, real readings (avg per hour)
conn_hr = sqlite3.connect(DB_PATH)
hourly_snap = pd.read_sql_query("""
    SELECT strftime('%Y-%m-%d %H:00', ts) AS hour,
           ROUND(AVG(bikes_regular),  1) AS reg,
           ROUND(AVG(bikes_electric), 1) AS elec,
           ROUND(AVG(bikes_regular + bikes_electric), 1) AS avail
    FROM snapshots
    WHERE ts >= datetime('now', '-48 hours')
      AND station_name NOT IN (""" + _BL_SQL + """)
    GROUP BY strftime('%Y-%m-%d %H:00', ts)
    ORDER BY hour
""", conn_hr)
conn_hr.close()
if not hourly_snap.empty:
    hr_labels = [r['hour'][11:13] for _, r in hourly_snap.iterrows()]  # "HH"
    hr_a   = [round(float(v),1) for v in hourly_snap['reg']]
    hr_b   = [round(float(v),1) for v in hourly_snap['elec']]
    hr_avail = [round(float(v),1) for v in hourly_snap['avail']]
else:
    hr_labels = []; hr_a = []; hr_b = []; hr_avail = []

# Hourly rides labels always use HH format
rhr_labels = [h[11:13] for h in hourly_rides_df['hour']]
# legacy today variable still needed below
today = daily_rides['date'].max() if not daily_rides.empty else ''
today_str = today

# Weekly rides (sum by week) — up to 16 weeks with real labels
daily_rides['week'] = pd.to_datetime(daily_rides['date']).dt.to_period('W-SAT')
weekly_agg = daily_rides.groupby('week')[['reg','elec']].sum().reset_index().tail(16)
wr_a = [int(v) for v in weekly_agg['reg']] if not weekly_agg.empty else []
wr_b = [int(v) for v in weekly_agg['elec']] if not weekly_agg.empty else []
wr_labels = []
for p in (weekly_agg['week'] if not weekly_agg.empty else []):
    ed = p.start_time.date()
    wr_labels.append(f'{ed.day}/{ed.month}')

# Monthly rides (sum by month) with Hebrew labels
_MONTHS_HE = {'1':'ינואר','2':'פברואר','3':'מרץ','4':'אפריל','5':'מאי',
               '6':'יוני','7':'יולי','8':'אוגוסט','9':'ספטמבר',
               '10':'אוקטובר','11':'נובמבר','12':'דצמבר'}
daily_rides['month'] = pd.to_datetime(daily_rides['date']).dt.to_period('M')
monthly_agg = daily_rides.groupby('month')[['reg','elec']].sum().reset_index()
mo_a = [int(v) for v in monthly_agg['reg']] if not monthly_agg.empty else []
mo_b = [int(v) for v in monthly_agg['elec']] if not monthly_agg.empty else []
mo_labels = [_MONTHS_HE.get(str(p.month), str(p)) for p in (monthly_agg['month'] if not monthly_agg.empty else [])]

# Avg/Median DATA (daily)
net_dates = list(net['date'])
net_labels, net_sundays = make_labels_and_sundays(net_dates)
avg_a = [round(float(v), 2) for v in net['avg_available']]
avg_b = [round(float(v), 2) for v in net['median_available']]

# Malfunction trend — show as % of total fleet (same as original index.html)
malf_a   = [round(float(v) / total_fleet * 100, 1) for v in net['total_disabled']]
malf_abs = [round(float(v)) for v in net['total_disabled']]

# Empty trend (% stations empty)
empty_a = [round(float(v) / active_stations * 100, 1) for v in net['empty_stations']]
empty_b = [round(float(v) / active_stations * 100, 1) for v in net['no_electric_stations']]

# Weekly/monthly aggregations for net charts
net['date_p'] = pd.to_datetime(net['date'])
net['week_p']  = net['date_p'].dt.to_period('W-SAT')
net['month_p'] = net['date_p'].dt.to_period('M')

net_weekly  = net.groupby('week_p')[['avg_available','median_available','total_disabled','empty_stations','no_electric_stations']].mean().reset_index().tail(16)
nw_labels = [f"{p.start_time.date().day}/{p.start_time.date().month}" for p in net_weekly['week_p']]
avg_w_a  = [round(float(v),2) for v in net_weekly['avg_available']]
avg_w_b  = [round(float(v),2) for v in net_weekly['median_available']]
malf_w_a   = [round(float(v) / total_fleet * 100, 1) for v in net_weekly['total_disabled']]
malf_w_abs = [round(float(v)) for v in net_weekly['total_disabled']]
empty_w_a= [round(float(v)/active_stations*100,1) for v in net_weekly['empty_stations']]
empty_w_b= [round(float(v)/active_stations*100,1) for v in net_weekly['no_electric_stations']]

net_monthly = net.groupby('month_p')[['avg_available','median_available','total_disabled','empty_stations','no_electric_stations']].mean().reset_index()
nm_labels = [_MONTHS_HE.get(str(p.month), str(p)) for p in net_monthly['month_p']]
avg_m_a  = [round(float(v),2) for v in net_monthly['avg_available']]
avg_m_b  = [round(float(v),2) for v in net_monthly['median_available']]
malf_m_a   = [round(float(v) / total_fleet * 100, 1) for v in net_monthly['total_disabled']]
malf_m_abs = [round(float(v)) for v in net_monthly['total_disabled']]
empty_m_a= [round(float(v)/active_stations*100,1) for v in net_monthly['empty_stations']]
empty_m_b= [round(float(v)/active_stations*100,1) for v in net_monthly['no_electric_stations']]

# Station chart: pick busiest station, pull real per-station daily reg/elec
busiest_station = snap.iloc[0]['station_name'] if not snap.empty else ''
conn_st = sqlite3.connect(DB_PATH)
st_df = pd.read_sql_query("""
    SELECT DATE(ts) AS date,
           ROUND(AVG(bikes_regular), 1)  AS reg,
           ROUND(AVG(bikes_electric), 1) AS elec
    FROM snapshots
    WHERE station_name = ?
    GROUP BY DATE(ts)
    ORDER BY DATE(ts)
""", conn_st, params=(busiest_station,))

# Build per-station daily history for all stations
all_st_df = pd.read_sql_query(f"""
    SELECT station_name, DATE(ts) AS date,
           ROUND(AVG(bikes_regular), 1)  AS reg,
           ROUND(AVG(bikes_electric), 1) AS elec
    FROM snapshots
    WHERE station_name NOT IN ({_BL_SQL})
    GROUP BY station_name, DATE(ts)
    ORDER BY station_name, date
""", conn_st)
conn_st.close()

daily_stations = {}
for sname, grp in all_st_df.groupby('station_name'):
    dates  = list(grp['date'])
    labs, suns = make_labels_and_sundays(dates)
    daily_stations[sname] = {
        'labels': labs,
        'sunday': suns,
        'a': [round(float(v), 1) for v in grp['reg']],
        'b': [round(float(v), 1) for v in grp['elec']],
    }

st_dates  = list(st_df['date'])
st_labels, st_sundays = make_labels_and_sundays(st_dates)
st_a      = [round(float(v), 1) for v in st_df['reg']]   # regular → teal series a
st_b      = [round(float(v), 1) for v in st_df['elec']]  # electric → gold series b

data_obj = {
    'station': {
        'daily': {
            'labels': st_labels, 'dates': st_dates, 'sunday': st_sundays,
            'a': st_a, 'b': st_b
        },
        'hourly':  {'labels': hr_labels, 'a': hr_a, 'b': hr_b},
        'weekly':  {'labels': wr_labels, 'a': wr_a, 'b': wr_b},
        'monthly': {'labels': mo_labels, 'a': mo_a, 'b': mo_b}
    },
    'rides': {
        'daily': {
            'labels': dr_labels, 'dates': list(recent_rides['date']),
            'sunday': dr_sundays, 'a': dr_a, 'b': dr_b
        },
        'hourly':  {'labels': rhr_labels, 'a': rhr_a, 'b': rhr_b},
        'weekly':  {'labels': wr_labels, 'a': wr_a, 'b': wr_b},
        'monthly': {'labels': mo_labels, 'a': mo_a, 'b': mo_b}
    },
    'malf': {
        'daily':   {'labels': net_labels, 'dates': net_dates, 'sunday': net_sundays, 'a': malf_a,   'c': malf_abs},
        'hourly':  {'labels': net_labels[-48:], 'a': malf_a[-48:],   'c': malf_abs[-48:]},
        'weekly':  {'labels': nw_labels, 'a': malf_w_a,   'c': malf_w_abs},
        'monthly': {'labels': nm_labels, 'a': malf_m_a,   'c': malf_m_abs}
    },
    'empty': {
        'daily':   {'labels': net_labels, 'dates': net_dates, 'sunday': net_sundays, 'a': empty_a, 'b': empty_b},
        'hourly':  {'labels': net_labels[-48:], 'a': empty_a[-48:], 'b': empty_b[-48:]},
        'weekly':  {'labels': nw_labels, 'a': empty_w_a, 'b': empty_w_b},
        'monthly': {'labels': nm_labels, 'a': empty_m_a, 'b': empty_m_b}
    },
    'avg': {
        'daily': {
            'labels': net_labels, 'dates': net_dates, 'sunday': net_sundays,
            'a': avg_a, 'b': avg_b
        },
        'hourly':  {'labels': net_labels[-48:], 'a': avg_a[-48:], 'b': avg_b[-48:]},
        'weekly':  {'labels': nw_labels, 'a': avg_w_a, 'b': avg_w_b},
        'monthly': {'labels': nm_labels, 'a': avg_m_a, 'b': avg_m_b}
    },
    'stations_geo':     stations_geo,
    'shabbat_stations': list(_SHAB_SET),
    'daily_stations':   daily_stations,
    'station_names':    sorted(daily_stations.keys()),
}
data_js = 'var DATA = ' + json.dumps(data_obj, ensure_ascii=False) + ';'

# First date for slider tooltip
conn_fd = sqlite3.connect(DB_PATH)
fd_row = conn_fd.execute('SELECT MIN(DATE(ts)) FROM snapshots').fetchone()
conn_fd.close()
first_date_iso = fd_row[0] if fd_row and fd_row[0] else ''
first_date_disp = (first_date_iso[8:].lstrip('0') + '/' +
                   first_date_iso[5:7].lstrip('0') + '/' +
                   first_date_iso[:4]) if first_date_iso else ''


# ── Donut chart SVG path ──────────────────────────────────────────────────────
def donut_arcs(elec_pct):
    """Return (elec_path, reg_path) SVG arc attributes for the half-donut."""
    cx, cy, r = 130, 122, 84
    total_deg = 180
    elec_deg = elec_pct / 100 * total_deg
    # start = left end of semicircle (angle = 180°)
    # end of elec arc = 180 - elec_deg
    import math
    def pt(deg):
        rad = math.radians(deg)
        return (round(cx + r * math.cos(rad), 2), round(cy - r * math.sin(rad), 2))
    start = pt(180)
    mid = pt(180 - elec_deg)
    end = pt(0)
    large_elec = 1 if elec_deg > 180 else 0
    large_reg  = 1 if (180 - elec_deg) > 180 else 0
    elec_path = f"M {start[0]} {start[1]} A {r} {r} 0 {large_elec} 1 {mid[0]} {mid[1]}"
    reg_path  = f"M {mid[0]} {mid[1]} A {r} {r} 0 {large_reg} 1 {end[0]} {end[1]}"
    return elec_path, reg_path

elec_arc, reg_arc = donut_arcs(elec_pct)

# Date range for UI defaults
d_to = today if today else '2026-07-12'
d_from_14 = (datetime.strptime(d_to, '%Y-%m-%d') - timedelta(days=14)).strftime('%Y-%m-%d')
d_from_10 = (datetime.strptime(d_to, '%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')
h_to   = hebrew_date(d_to)
h_from = hebrew_date(d_from_14)
h_from10 = hebrew_date(d_from_10)


# ── Patch HTML ────────────────────────────────────────────────────────────────
with open(os.path.join(BASE_DIR, 'jerufun-redesign-proposal.html'), 'r', encoding='utf-8') as f:
    html = f.read()

# 0. Embed Heebo font as base64 so it works in CSP-restricted environments (Artifact viewer)
_HEEBO_WEIGHTS = {
    400: 'https://fonts.gstatic.com/s/heebo/v28/NGSpv5_NC0k9P_v6ZUCbLRAHxK1EiSyccg.ttf',
    600: 'https://fonts.gstatic.com/s/heebo/v28/NGSpv5_NC0k9P_v6ZUCbLRAHxK1EVyuccg.ttf',
    700: 'https://fonts.gstatic.com/s/heebo/v28/NGSpv5_NC0k9P_v6ZUCbLRAHxK1Ebiuccg.ttf',
    800: 'https://fonts.gstatic.com/s/heebo/v28/NGSpv5_NC0k9P_v6ZUCbLRAHxK1ECSuccg.ttf',
}
_font_faces = []
for _w, _url in _HEEBO_WEIGHTS.items():
    try:
        with urllib.request.urlopen(_url, timeout=10) as _r:
            _b64 = base64.b64encode(_r.read()).decode()
        _font_faces.append(
            f"@font-face{{font-family:'Heebo';font-style:normal;font-weight:{_w};"
            f"font-display:swap;src:url(data:font/truetype;base64,{_b64}) format('truetype');}}"
        )
    except Exception:
        pass  # network unavailable — fall back to Google Fonts link

if _font_faces:
    _embedded_style = '<style>\n' + '\n'.join(_font_faces) + '\n</style>'
    html = html.replace(
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        _embedded_style + '\n<!-- Google Fonts replaced with embedded font -->'
        + '\n<script>window._jeruGeo=' + json.dumps(stations_geo, ensure_ascii=False)
        + ';window._jeruShabbat=' + json.dumps(list(_SHAB_SET), ensure_ascii=False) + ';</script>'
        + '\n<script src="https://maps.googleapis.com/maps/api/js?key=__MAPS_KEY__&callback=initMap&loading=async" async defer></script>',
    ).replace(
        '<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">',
        ''
    )

# 1. Page title
html = html.replace('<title>ירופאן — הצעת עיצוב חדשה</title>',
                    '<title>ירופאן — דשבורד ירושלים (ניסיון עיצוב)</title>')

# 2. Topbar timestamp
html = html.replace(
    'עדכון אחרון: 14:32 17/7',
    f'עדכון אחרון: {last_update}'
)

# 3. KPI card: אופניים זמינים (format: total / available, with delta)
avail_delta_cls = 'good' if avail_delta >= 0 else 'bad'
avail_delta_arrow = '▲' if avail_delta >= 0 else '▼'
avail_delta_html = (
    f'<div class="kpi-delta {avail_delta_cls}">'
    f'<span class="arrow">{avail_delta_arrow}</span>'
    f'<span class="value num">{abs(avail_delta)}%</span>'
    f'<span class="ctx">לעומת שבוע שעבר באותה שעה</span></div>'
) if wow_fleet > 0 else ''

html = re.sub(
    r'(<div class="kpi gold">.*?<div class="kpi-value num">)[^<]*(</div>.*?<div class="kpi-sub num">)[^<]*(</div>)',
    lambda m: m.group(1) + f'{avail_pct}%' + m.group(2) + f'{total_fleet:,} / {total_available:,} אופניים' + m.group(3),
    html, count=1, flags=re.DOTALL
)
# Replace existing delta with real one
html = re.sub(
    r'(<div class="kpi gold">.*?kpi-sub num.*?</div>)\s*<div class="kpi-delta[^"]*">.*?</div>',
    r'\1\n      ' + avail_delta_html,
    html, count=1, flags=re.DOTALL
)

# 4. KPI: תחנות פעילות
html = re.sub(
    r'(<div class="kpi teal">\s*<div class="kpi-label"><span class="name">תחנות פעילות.*?<div class="kpi-value num">)[^<]*(</div>)',
    lambda m: m.group(1) + str(active_stations) + m.group(2),
    html, count=1, flags=re.DOTALL
)

# 5. KPI: תחנות ריקות
html = re.sub(
    r'(<div class="kpi red">.*?<div class="kpi-value num">)[^<]*(</div>)',
    lambda m: m.group(1) + str(empty_stations) + m.group(2),
    html, count=1, flags=re.DOTALL
)

# 6. KPI: אופניים תקולים (format: total / disabled)
html = re.sub(
    r'(<div class="kpi slate">.*?<div class="kpi-value num">)[^<]*(</div>.*?<div class="kpi-sub num">)[^<]*(</div>)',
    lambda m: m.group(1) + f'{disabled_pct}%' + m.group(2) + f'{total_fleet:,} / {total_disabled:,} אופניים' + m.group(3),
    html, count=1, flags=re.DOTALL
)

# 7. KPI: נסיעות יומי
html = re.sub(
    r'(מספר נסיעות יומי.*?<div class="kpi-value num">)[^<]*(</div>)',
    lambda m: m.group(1) + f'{today_rides:,}' + m.group(2),
    html, count=1, flags=re.DOTALL
)

# 8. Donut chart
html = re.sub(
    r'<path d="M 46 122 A 84 84[^"]*" stroke="var\(--gold\)"[^/]*/>\s*<path d="M [^"]*" stroke="var\(--teal\)"[^/]*/>',
    f'<path d="{elec_arc}" stroke="var(--gold)" stroke-width="30" stroke-linecap="butt"/>\n'
    f'            <path d="{reg_arc}" stroke="var(--teal)" stroke-width="30" stroke-linecap="butt"/>',
    html, count=1
)
html = re.sub(
    r'(<div class="donut-center">\s*<div class="n num">)[^<]*(</div>)',
    lambda m: m.group(1) + f'{total_available:,}' + m.group(2),
    html, count=1
)
html = re.sub(
    r'(<div class="leg electric">⚡ <span class="num">)[^<]*(</span>)',
    lambda m: m.group(1) + f'{elec_pct}%' + m.group(2),
    html, count=1
)
html = re.sub(
    r'(<div class="leg regular">🚲 <span class="num">)[^<]*(</span>)',
    lambda m: m.group(1) + f'{reg_pct}%' + m.group(2),
    html, count=1
)

# 9. Station search placeholder
html = html.replace(
    'value="מחנה יהודה — שער יפו"',
    f'value="{busiest_station}"',
    1
)

# 10. Ranking table body
html = re.sub(
    r'<tbody id="rankingBody">.*?</tbody>',
    f'<tbody id="rankingBody">\n{ranking_tbody}\n</tbody>',
    html, count=1, flags=re.DOTALL
)

# 11. Rides skyline bars
html = re.sub(
    r'<div class="skyline" id="ridesSkyline">[\s\S]*?</div>\s*(?=<div class="skyline-labels")',
    f'<div class="skyline" id="ridesSkyline">\n{rides_skyline_html}\n</div>\n      ',
    html, count=1
)
# Rides labels
html = re.sub(
    r'<div class="skyline-labels" id="ridesSkylineLabels">.*?</div>',
    f'<div class="skyline-labels" id="ridesSkylineLabels">\n{rides_labels_html}\n</div>',
    html, count=1, flags=re.DOTALL
)

# 12. Chronic grid
html = re.sub(
    r'<div class="chronic-grid">.*?</div>(?=\s*</section>)',
    f'<div class="chronic-grid">\n{chronic_grid_html}\n</div>',
    html, count=1, flags=re.DOTALL
)

# 13. Distribution histogram bars
html = re.sub(
    r'<div class="hist">.*?</div>(?=\s*<div class="skyline-labels")',
    f'<div class="hist">\n{hist_html}\n</div>',
    html, count=1, flags=re.DOTALL
)

# 14. Slider tooltip injection
_slider_tip = (
    f'שעתי: עד 48 שעות אחרונות · יומי: עד 30 ימים · '
    f'שבועי: עד 16 שבועות · חודשי: עד 12 חודשים · '
    f'נתונים זמינים מאז {first_date_disp}'
)
html = html.replace('__SLIDER_TIP__', _slider_tip)

# 15. Replace stations JS array
html = re.sub(
    r'var stations = \[[\s\S]*?\];',
    stations_js,
    html, count=1
)

# 16. Replace DATA object
html = re.sub(
    r'var DATA = \{[\s\S]*?\};',
    data_js,
    html, count=1
)

# 17. Update section-02 station name label
html = html.replace(
    'value="מחנה יהודה — שער יפו"',
    f'value="{busiest_station}"'
)

# 17b. Inject JS date-filter-redraw before the "Initial render" comment
DATE_FILTER_JS = r"""
    // ── LIVE DATE FILTER REDRAW (injected) ─────────────────────────────────
    (function(){
      function filterAndRedraw(chartKey, updateFn, fromISO, toISO){
        var d = DATA[chartKey]['daily'];
        var dates = d.dates || [];
        if (!dates.length){ updateFn('daily'); return; }
        var fl = { labels:[], dates:[], a:[], sunday:[] };
        if (d.b) fl.b = [];
        dates.forEach(function(dt, i){
          if (dt >= fromISO && dt <= toISO){
            fl.labels.push(d.labels[i]);
            fl.dates.push(dt);
            fl.a.push(d.a[i]);
            if (d.b) fl.b.push(d.b[i]);
          }
        });
        fl.sunday = fl.dates.reduce(function(acc, dt, i){
          if (new Date(dt).getDay() === 0) acc.push(i);
          return acc;
        }, []);
        var orig = DATA[chartKey]['daily'];
        DATA[chartKey]['daily'] = fl;
        updateFn('daily');
        DATA[chartKey]['daily'] = orig;
      }

      var FILTER_MAP = [
        ['stationDateFrom','stationDateTo','stationDateApply','station', updateStation],
        ['ridesDateFrom',  'ridesDateTo',  'ridesDateApply',  'rides',   updateRides ],
        ['malfDateFrom',   'malfDateTo',   'malfDateApply',   'malf',    updateMalf  ],
        ['emptyDateFrom',  'emptyDateTo',  'emptyDateApply',  'empty',   updateEmpty ],
        ['avgDateFrom',    'avgDateTo',    'avgDateApply',    'avg',     updateAvg   ]
      ];
      FILTER_MAP.forEach(function(row){
        var fromEl = document.getElementById(row[0]);
        var toEl   = document.getElementById(row[1]);
        var btn    = document.getElementById(row[2]);
        if (!btn || !fromEl || !toEl) return;
        btn.addEventListener('click', function(){
          if (fromEl.value && toEl.value && fromEl.value <= toEl.value)
            filterAndRedraw(row[3], row[4], fromEl.value, toEl.value);
        });
      });
    })();
    // ── end injected ────────────────────────────────────────────────────────
"""
html = html.replace(
    '    // Initial render (daily view, matching the active button on load)',
    DATE_FILTER_JS + '    // Initial render (daily view, matching the active button on load)'
)


# ── Inject Google Maps API key ────────────────────────────────────────────────
try:
    from api_keys import GOOGLE_MAPS_KEY as _MAPS_KEY
except ImportError:
    _MAPS_KEY = os.environ.get('GOOGLE_MAPS_KEY', '')
html = html.replace('__MAPS_KEY__', _MAPS_KEY)

# ── Malf trend-figure placeholders ────────────────────────────────────────────
# Use last graph point (daily average) so card matches what the chart shows
_card_pct = malf_a[-1] if malf_a else disabled_pct
_card_abs = int(malf_abs[-1]) if malf_abs else total_disabled
html = html.replace('__MALF_VAL__', f'{_card_abs:,} אופניים תקולים ({_card_pct}%)')

# Delta: compare today's daily average to the daily average 7 days ago
_today_date = _now.strftime('%Y-%m-%d')
_wow_date   = (_now - timedelta(days=7)).strftime('%Y-%m-%d')
if _wow_date in net_dates:
    _wow_idx      = net_dates.index(_wow_date)
    _wow_pct      = malf_a[_wow_idx]
    _malf_delta   = round(_card_pct - _wow_pct, 1)
else:
    _malf_delta   = round(disabled_pct - wow_disabled_pct)  # fallback to snapshot comparison
_delta_cls  = 'up' if _malf_delta > 0 else 'down'
_delta_sign = '+' if _malf_delta > 0 else '−'
html = html.replace('__MALF_DELTA_CLS__', _delta_cls)
html = html.replace('__MALF_DELTA__', f'{_delta_sign} {abs(_malf_delta)}%')

# ── Write output ──────────────────────────────────────────────────────────────
out_path = os.path.join(BASE_DIR, 'index2.html')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"✅ נוצר: {out_path}")
print(f"   תחנות: {active_stations} | זמינות: {total_available:,} ({avail_pct}%) | ריקות: {empty_stations}")
print(f"   ⚡ {total_electric:,} חשמליים ({elec_pct}%) | 🚲 {total_regular:,} רגילים ({reg_pct}%)")
print(f"   🔧 תקולים: {total_disabled:,} ({disabled_pct}%) | 🚴 נסיעות היום: {today_rides}")
print(f"   עדכון אחרון: {last_update}")
