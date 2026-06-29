"""
generate_dashboard.py
Reads from jerufun.db → writes self-contained dashboard.html.
Usage:  python generate_dashboard.py [--output path/to/file.html]
"""
import argparse
import json
import os
from datetime import datetime, timezone, timedelta

IL_TZ = timezone(timedelta(hours=3))

import requests

import db
from config import MAP_URL
try:
    from api_keys import GOOGLE_MAPS_KEY
except ImportError:
    GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_KEY", "")

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "index.html")
ICONS_DIR  = os.path.join(os.path.dirname(__file__), "icons")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JeruFunMonitor/1.0"})

YELLOW = "#f0b429"   # electric
BLUE   = "#2980b9"   # regular


def _read_svgs() -> dict:
    """Load the three JeruFun station SVG icons from disk."""
    result = {}
    for key, fname in [("active", "station-bike-active.svg"),
                       ("none",   "station-bike-none.svg"),
                       ("inactive","station-bike-inactive.svg")]:
        path = os.path.join(ICONS_DIR, fname)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                result[key] = f.read().strip()
        else:
            result[key] = ""
    return result


# ── data assembly ──────────────────────────────────────────────────────────────

def _fetch_map_meta() -> dict:
    try:
        resp = SESSION.get(MAP_URL, timeout=10)
        resp.raise_for_status()
        return {s["id"]: {"lat": s.get("location", {}).get("lat", 0),
                           "lng": s.get("location", {}).get("lng", 0)}
                for s in resp.json().get("stations", [])}
    except Exception:
        return {}


def _histogram(snapshot_df, field_fn):
    """Build bucket + cumulative arrays from a per-station values Series."""
    vals = snapshot_df.apply(field_fn, axis=1)
    buckets = {"0": 0, "1-2": 0, "3-4": 0, "5-6": 0, "7-8": 0, "9-10": 0, "11+": 0}
    bucket_names = list(buckets.keys())
    for v in vals:
        if v == 0:          buckets["0"]    += 1
        elif v <= 2:        buckets["1-2"]  += 1
        elif v <= 4:        buckets["3-4"]  += 1
        elif v <= 6:        buckets["5-6"]  += 1
        elif v <= 8:        buckets["7-8"]  += 1
        elif v <= 10:       buckets["9-10"] += 1
        else:               buckets["11+"]  += 1

    # cumulative: % of stations with AT MOST t bikes
    total = len(vals)
    max_val = int(vals.max()) if not vals.empty else 15
    cumulative = []
    for t in range(0, min(max_val + 2, 20)):
        pct = round(100 * int((vals <= t).sum()) / total, 1) if total > 0 else 0
        cumulative.append(pct)

    return {"labels": bucket_names, "counts": list(buckets.values()), "cumulative": cumulative}


SHABBAT_STATIONS = {
    "מרכז מורשת בגין", "התיאטרון", "מוזיאון ישראל", "קניון הדר",
    "האוניברסיטה העברית הר הצופים", "כפר הסטודנטים הר הצופים",
    "רמי לוי-תלפיות", "חוות הנוער הציוני", "קניון עזריאלי (מלחה)",
    "רמת בית הכרם", "מכללת עזריאלי להנדסה", "האוניברסיטה",
    "בנייני האומה", "כיכר דניה", "בנק ישראל", 'גן החיות התנ"כי',
    "גן בלומפילד",
}


def build_data() -> dict:
    latest       = db.get_latest_snapshot()
    daily_net    = db.get_daily_network_summary()
    daily_sta    = db.get_all_daily_station_summary()
    hourly_sta   = db.get_hourly_timeseries_all(hours=72)
    map_meta     = _fetch_map_meta()
    addresses    = db.get_all_station_addresses()
    _, last_ts   = db.get_collection_range()

    # ── stations geo ──
    stations_geo = []
    for _, row in latest.iterrows():
        meta = map_meta.get(row["station_id"], {})
        avail = int(row["bikes_regular"]) + int(row["bikes_electric"])
        stations_geo.append({
            "id":       row["station_id"],
            "name":     row["station_name"],
            "address":  addresses.get(row["station_id"], ""),
            "lat":      meta.get("lat", 0),
            "lng":      meta.get("lng", 0),
            "regular":  int(row["bikes_regular"]),
            "electric": int(row["bikes_electric"]),
            "available": avail,
            "disabled": int(row["bikes_disabled"]),
            "docks":    int(row["docks_free"]),
        })

    # ── KPIs ──
    total_avail   = sum(s["available"] for s in stations_geo)
    total_elec    = sum(s["electric"]  for s in stations_geo)
    total_reg     = sum(s["regular"]   for s in stations_geo)
    total_dis     = sum(s["disabled"]  for s in stations_geo)
    n_stations    = len(stations_geo)
    empty_sta     = sum(1 for s in stations_geo if s["available"] == 0)
    no_elec_sta   = sum(1 for s in stations_geo if s["electric"]  == 0)
    avg_per_sta   = round(total_avail / n_stations, 1) if n_stations else 0
    import statistics
    vals_list     = [s["available"] for s in stations_geo]
    median_per_sta = round(statistics.median(vals_list), 1) if vals_list else 0

    # ── histograms ──
    hist_all  = _histogram(latest, lambda r: r["bikes_regular"] + r["bikes_electric"])
    hist_elec = _histogram(latest, lambda r: r["bikes_electric"])
    hist_reg  = _histogram(latest, lambda r: r["bikes_regular"])

    # ── daily network series ──
    if not daily_net.empty:
        dn_rows = daily_net.to_dict(orient="records")
    else:
        dn_rows = []

    return {
        "meta": {
            "generated_at":   datetime.now(IL_TZ).strftime("%d/%m/%Y %H:%M"),
            "last_collection": last_ts or "—",
            "total_stations":  n_stations,
        },
        "kpis": {
            "total_available":    total_avail,
            "electric":           total_elec,
            "regular":            total_reg,
            "disabled":           total_dis,
            "pct_electric":       int(round(total_elec / total_avail * 100)) if total_avail else 0,
            "pct_regular":        int(round(total_reg  / total_avail * 100)) if total_avail else 0,
            "avg_per_station":    avg_per_sta,
            "median_per_station": median_per_sta,
            "empty_stations":     empty_sta,
            "no_electric_stations": no_elec_sta,
            "active_stations":    n_stations,
            "total_bikes":        total_avail + total_dis,
            "pct_disabled":       int(round(total_dis / (total_avail + total_dis) * 100)) if (total_avail + total_dis) else 0,
        },
        "stations_geo": stations_geo,
        "histogram": {"all": hist_all, "electric": hist_elec, "regular": hist_reg},
        "daily_network": dn_rows,
        "daily_stations":  daily_sta,
        "hourly_stations": hourly_sta,
        "station_names": sorted(s["name"] for s in stations_geo),
        "shabbat_stations": sorted(SHABBAT_STATIONS & {s["name"] for s in stations_geo}),
    }


# ── HTML template ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ירופאן — מוניטור תחנות</title>
<link rel="icon" type="image/png" href="facivon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://maps.googleapis.com/maps/api/js?key=__MAPS_KEY__&callback=initMap" async defer></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
/* ── PostHog design tokens ───────────────────────────────────────────────── */
:root {
  --canvas:       #eeefe9;   /* warm cream page bg */
  --surface:      #ffffff;   /* card surface */
  --surface-soft: #e5e7e0;   /* subtle fills */
  --surface-dark: #23251d;   /* header / dark elements */
  --ink:          #23251d;   /* headline text */
  --body:         #4d4f46;   /* body text */
  --muted:        #6c6e63;   /* secondary text */
  --ash:          #9b9c92;   /* placeholders */
  --hairline:     #bfc1b7;   /* borders */
  --hairline-soft:#dcdfd2;   /* in-card dividers */
  --primary:      #f7a501;   /* yellow-orange CTA — also electric bike */
  --primary-dark: #dd9001;
  --blue:         #2c84e0;   /* regular bike */
  --blue-soft:    #dceaf6;
  --green:        #2c8c66;
  --green-soft:   #d9eddf;
  --red:          #cd4239;
  --red-soft:     #f7d6d3;
  --gray:         #6c6e63;
  --radius:       6px;
  --pill:         9999px;
  --header-h:     56px;
  --toc-w:        210px;
  --font:         'IBM Plex Sans', -apple-system, system-ui, sans-serif;
}

*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--canvas);color:var(--body);
  direction:rtl;font-size:15px;line-height:1.5}

/* ── header ── */
.hdr{position:sticky;top:0;z-index:900;background:var(--surface-dark);color:#fff;
  height:var(--header-h);display:flex;align-items:center;gap:14px;padding:0 24px;
  border-bottom:1px solid rgba(255,255,255,.08)}
.hdr .logo{font-size:20px;font-weight:800;letter-spacing:-.3px}
.hdr .logo span{color:var(--primary)}
.hdr .sub{font-size:12px;opacity:.55;margin-top:1px;font-weight:400}
.hdr .stamp{margin-right:auto;font-size:11px;opacity:.5;direction:ltr;
  text-align:left;font-weight:400}

/* ── layout ── */
.layout{max-width:1240px;padding:0 20px;margin:0 auto}
.page{padding:24px 0 72px;padding-right:calc(var(--toc-w) + 32px);min-width:0}

/* ── TOC — fixed floating sidebar ── */
.toc{position:fixed;top:140px;right:20px;
  width:var(--toc-w);background:var(--canvas);border:1px solid var(--hairline);
  border-radius:var(--radius);padding:10px 0;z-index:800;}
.toc .ttl{font-size:11px;font-weight:700;color:var(--ash);text-transform:uppercase;
  padding:0 14px 8px;letter-spacing:.9px}
.toc a{display:block;padding:6px 14px;font-size:13px;color:var(--muted);
  text-decoration:none;border-right:3px solid transparent;transition:all .12s;
  font-weight:500}
.toc a:hover{color:var(--ink);background:var(--surface-soft)}
.toc a.active{color:var(--ink);font-weight:700;border-right-color:var(--primary);
  background:var(--surface-soft)}

/* ── sections ── */
.sec{margin-bottom:40px;position:relative}
.sec-title{font-size:21px;font-weight:800;color:var(--ink);padding-bottom:10px;
  border-bottom:2px solid var(--hairline-soft);margin-bottom:20px;
  scroll-margin-top:70px;line-height:1.2}

/* ── cards ── */
.card{background:var(--surface);border:1px solid var(--hairline);
  border-radius:var(--radius);padding:20px;margin-bottom:16px;position:relative}
/* ── info button ── */
.info-btn{position:absolute;left:10px;top:10px;width:18px;height:18px;border-radius:50%;
  background:var(--hairline);color:var(--muted);font-size:11px;font-weight:700;
  font-family:Georgia,serif;display:flex;align-items:center;justify-content:center;
  cursor:default;z-index:10;user-select:none;line-height:1}
.info-btn:hover{background:var(--primary);color:#fff}
.info-btn .tip{display:none;position:absolute;left:0;top:24px;
  background:#23251d;color:#f5f5f0;font-size:12px;line-height:1.6;
  padding:10px 13px;border-radius:7px;width:280px;direction:rtl;text-align:right;
  z-index:500;white-space:normal;font-weight:400;
  font-family:'IBM Plex Sans',sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25)}
.info-btn:hover .tip{display:block}
.card-title{font-size:13px;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.6px;margin-bottom:14px}
.row{display:flex;gap:16px;flex-wrap:wrap}
.row .card{flex:1;min-width:240px}

/* ── KPI grid ── */
.kpi-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}
/* ── gauge inside kpi card ── */
.gauge-wrap{margin-top:0;direction:ltr}
.gauge-clip{overflow:hidden;aspect-ratio:2/1}
.gauge-labels{display:flex;justify-content:space-between;
  font-size:11px;font-weight:700;padding:3px 2px 0}
.kpi{background:var(--surface);border:1px solid var(--hairline);
  border-radius:var(--radius);padding:8px 10px;border-top:3px solid var(--hairline);
  display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}
.kpi.g  {border-top-color:var(--green)}
.kpi.y  {border-top-color:var(--primary)}
.kpi.b  {border-top-color:var(--blue)}
.kpi.r  {border-top-color:var(--red)}
.kpi.gray{border-top-color:var(--ash)}
.kpi .v {font-size:22px;font-weight:800;line-height:1;color:var(--ink)}
.kpi .l {font-size:12px;color:var(--muted);margin-top:4px;font-weight:600;
  text-transform:uppercase;letter-spacing:.5px}

/* ── PostHog pill toggles ── */
.toggle-group{display:inline-flex;background:var(--surface-soft);
  border-radius:var(--pill);padding:3px;margin-bottom:14px;gap:2px}
.toggle-group button{padding:5px 16px;font-size:13px;border:none;
  background:transparent;color:var(--muted);cursor:pointer;
  border-radius:var(--pill);transition:all .15s;font-family:var(--font);
  font-weight:600;white-space:nowrap}
.toggle-group button.active{background:var(--surface-dark);color:#fff}
.toggle-group button:hover:not(.active){background:var(--hairline);color:var(--ink)}

/* ── callout banners ── */
.banner{border-radius:var(--radius);padding:12px 16px;font-size:13px;
  font-weight:500;margin-bottom:14px}
.banner.green{background:var(--green-soft);color:var(--ink)}
.banner.red  {background:var(--red-soft);  color:var(--ink)}
.banner.blue {background:var(--blue-soft); color:var(--ink)}

/* ── map ── */
#map-el{height:460px;border-radius:var(--radius);border:1px solid var(--hairline)}
.gm-style .gm-style-iw-c{padding:0 !important;min-height:unset !important;border-radius:8px !important}
.gm-style .gm-style-iw-d{overflow:auto !important;padding:12px 14px !important}
.gm-style .gm-style-iw-chr{display:none !important}
.legend-row{display:flex;gap:16px;margin-top:10px;font-size:12px;
  color:var(--muted);flex-wrap:wrap;font-weight:500}
.legend-row .dot{width:10px;height:10px;border-radius:50%;
  display:inline-block;margin-left:5px;vertical-align:middle}

/* ── chart safety ── */
.chart-wrap{position:relative}
.chart-wrap canvas{max-height:72vh}

/* ── station select ── */
#sta-sel-wrap{position:relative;flex:1;min-width:200px}
#sta-search{width:100%;box-sizing:border-box;padding:8px 12px;
  border:1px solid var(--hairline);border-radius:var(--radius);
  font-size:14px;font-family:var(--font);font-weight:500;
  background:var(--surface);color:var(--ink);direction:rtl;text-align:right}
#sta-search:focus{outline:none;border-color:var(--primary);
  box-shadow:0 0 0 3px rgba(247,165,1,.18)}
#sta-suggestions{display:none;position:absolute;top:100%;right:0;left:0;
  background:var(--surface);border:1px solid var(--hairline);border-top:none;
  border-radius:0 0 var(--radius) var(--radius);list-style:none;
  max-height:240px;overflow-y:auto;z-index:900;margin:0;padding:0}
#sta-suggestions li{padding:8px 14px;cursor:pointer;font-size:13px;
  border-bottom:1px solid var(--hairline-soft);direction:rtl;text-align:right}
#sta-suggestions li:hover,#sta-suggestions li:active{background:var(--surface-soft)}

/* ── responsive ── */
@media(max-width:900px){
  .toc{display:none}
  .page{padding:16px 16px 48px;padding-right:16px}
}

@media(max-width:768px){
  .layout{padding:0}
  .kpi-grid{grid-template-columns:repeat(3,1fr)}
  .row{flex-direction:column}
  .row .card{min-width:unset}
  .hdr .logo{font-size:16px}
  .sec-title{font-size:17px}
  .toggle-group button{font-size:11px;padding:5px 10px}
  #map-el{height:320px}
  .chart-wrap canvas{max-height:260px}
}

@media(max-width:480px){
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  .kpi-grid>:first-child{grid-column:1/-1;max-width:320px;margin:0 auto;width:100%}
  .kpi .v{font-size:26px}
  .kpi .l{font-size:11px}
  .page{padding:12px 12px 48px}
  .sec-title{font-size:15px}
  .card{padding:14px}
  #map-el{height:260px}
  .toggle-group{flex-wrap:wrap}
  .toggle-group button{font-size:11px;padding:5px 8px}
  label[style*="align-items:center"]{flex-wrap:wrap}
  .chart-wrap{margin-left:-14px;margin-right:-14px;overflow:hidden}
  .rank-scroll{height:280px !important}
}
</style>
</head>
<body>

<header class="hdr">
  <div style="display:flex;align-items:center;gap:10px">
    <img src="facivon.png" alt="JeruFun" style="height:38px;width:38px;border-radius:50%;object-fit:cover">
    <div class="logo"><span>ירו</span>פאן מוניטור</div>
  </div>
  <div class="stamp" id="stamp"></div>
</header>

<div class="layout">

<nav class="toc">
  <div class="ttl">תוכן עניינים</div>
  <a href="#kpis">📊 מספרים כלליים</a>
  <a href="#map-sec">🗺️ מפה</a>
  <a href="#station-sec">🔍 תחנה ספציפית</a>
  <a href="#ranking">🏅 דירוג תחנות</a>
  <a href="#trend-dis">⚠️ טרנד תקולים</a>
  <a href="#trend-empty">🔴 טרנד תחנות ריקות</a>
  <a href="#chronic-empty">📋 תחנות כרוניות</a>
  <a href="#histogram">📈 התפלגות</a>
  <a href="#trend-avg">📉 טרנד ממוצע/חציון</a>
</nav>

<main class="page">

<!-- ══ 1. KPIs ══════════════════════════════════════════════════════════════ -->
<section class="sec" id="kpis">
  <div class="info-btn">i<span class="tip">המספרים בכרטיסיות אלו מבוססים על המדידה העדכנית ביותר שנאספה מהרשת.</span></div>
  <h2 class="sec-title">📊 מספרים כלליים</h2>
  <div class="kpi-grid" id="kpi-grid"></div>
</section>

<!-- ══ 2. MAP ═════════════════════════════════════════════════════════════════ -->
<section class="sec" id="map-sec">
  <h2 class="sec-title">🗺️ מפה אינטראקטיבית</h2>
  <div style="position:relative;margin-bottom:8px">
    <input id="map-search" type="text" placeholder="🔍 חיפוש תחנה..."
      style="width:100%;padding:9px 14px;border:1px solid var(--hairline);
             border-radius:var(--radius);font-size:14px;font-family:var(--font);
             background:var(--surface);color:var(--ink);direction:rtl;outline:none">
    <ul id="map-suggestions" style="display:none;position:absolute;top:100%;right:0;left:0;
        background:var(--surface);border:1px solid var(--hairline);border-top:none;
        border-radius:0 0 var(--radius) var(--radius);list-style:none;
        max-height:220px;overflow-y:auto;z-index:900;margin:0;padding:0"></ul>
  </div>
  <div id="map-el"></div>
  <div class="legend-row">
    <span><span class="dot" style="background:#27ae60"></span>זמינה</span>
    <span><span class="dot" style="background:#FF0000"></span>ריקה</span>
    <span><span class="dot" style="background:#808080"></span>מושבתת</span>
    <span><span style="color:#f0b429;font-size:13px;margin-left:3px">✡</span>פעילה בשבת לחשמליות</span>
  </div>
</section>

<!-- ══ 3. STATION DETAIL ═════════════════════════════════════════════════════ -->
<section class="sec" id="station-sec">
  <h2 class="sec-title">🔍 כמות אופניים בתחנה לאורך זמן</h2>
  <div class="card">
    <div class="info-btn">i<span class="tip">ממוצע יומי של אופניים זמינים (חשמלי ורגיל בנפרד) בתחנה הנבחרת. הממוצע מחושב על כל המדידות שנאספו באותו יום (פעם בשעה), ולכן עשוי להיות מספר עשרוני.</span></div>
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">
      <label style="font-weight:600;white-space:nowrap;font-size:13px">בחר תחנה:</label>
      <div id="sta-sel-wrap">
        <input id="sta-search" type="text" autocomplete="off" placeholder="חפש תחנה...">
        <ul id="sta-suggestions"></ul>
      </div>
      <div class="toggle-group" id="gran-sta">
        <button data-g="hourly">שעתי</button>
        <button class="active" data-g="daily">יומי</button>
        <button data-g="weekly">שבועי</button>
        <button data-g="monthly">חודשי</button>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="chart-sta" height="280"></canvas></div>
  </div>
</section>

<!-- ══ 4. RANKING ════════════════════════════════════════════════════════════ -->
<section class="sec" id="ranking">
  <h2 class="sec-title">🏅 דירוג תחנות לפי כמות אופניים</h2>
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px">
    <div class="toggle-group" id="rank-filter" style="margin-bottom:0">
      <button class="active" data-f="all">הכל</button>
      <button data-f="electric">⚡ חשמלי</button>
      <button data-f="regular">🚲 רגיל</button>
    </div>
    <div class="toggle-group" id="rank-sort" style="margin-bottom:0">
      <button class="active" data-s="desc">↓ הכי הרבה קודם</button>
      <button data-s="asc">↑ הכי פחות קודם</button>
    </div>
  </div>
  <div class="card" style="padding:16px 20px">
    <div class="info-btn">i<span class="tip">כמות האופניים הנוכחית בכל תחנה לפי המדידה האחרונה. ניתן לפלטר לפי סוג אופניים (חשמלי/רגיל) ולמיין לפי כמות עולה או יורדת.</span></div>
    <div class="rank-scroll" style="height:500px;overflow-y:auto">
      <div style="position:relative;height:2400px">
        <canvas id="rank-chart"></canvas>
      </div>
    </div>
  </div>
</section>

<!-- ══ 5. TREND DISABLED ════════════════════════════════════════════════════ -->
<section class="sec" id="trend-dis">
  <h2 class="sec-title">⚠️ טרנד אופניים תקולים לאורך זמן</h2>
  <div class="card">
    <div class="info-btn">i<span class="tip">סך האופניים התקולים ברשת כולה. הערך היומי הוא ממוצע כל המדידות שנאספו באותו יום (פעם בשעה) — לכן הוא עשוי להיות מספר עשרוני. למשל, אם בשעה אחת היו 200 תקולים ובשעה אחרת 220, הממוצע היומי יהיה 210.</span></div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div class="card-title" style="margin-bottom:0"></div>
      <div class="toggle-group" id="gran-dis">
        <button class="active" data-g="daily">יומי</button>
        <button data-g="weekly">שבועי</button>
        <button data-g="monthly">חודשי</button>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="chart-trend-dis" height="280"></canvas></div>
  </div>
</section>

<!-- ══ 6. TREND EMPTY ══════════════════════════════════════════════════════ -->
<section class="sec" id="trend-empty">
  <h2 class="sec-title">🔴 טרנד תחנות ריקות לאורך זמן</h2>
  <div class="card">
    <div class="info-btn">i<span class="tip">מספר התחנות שהיו ריקות לחלוטין (0 אופניים) או ללא אופניים חשמליים בכל יום. הערך היומי הוא ממוצע כל המדידות שנאספו באותו יום (פעם בשעה), ולכן עשוי להיות מספר עשרוני.</span></div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div class="card-title" style="margin-bottom:0"></div>
      <div class="toggle-group" id="gran-empty">
        <button class="active" data-g="daily">יומי</button>
        <button data-g="weekly">שבועי</button>
        <button data-g="monthly">חודשי</button>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="chart-trend-empty" height="280"></canvas></div>
  </div>
</section>

<!-- ══ 7. CHRONIC EMPTY ═════════════════════════════════════════════════════ -->
<section class="sec" id="chronic-empty">
  <h2 class="sec-title">📋 תחנות כרוניות ללא אופניים</h2>
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px">
    <div class="toggle-group" id="chronic-filter" style="margin-bottom:0">
      <button class="active" data-f="all">הכל</button>
      <button data-f="electric">⚡ חשמלי</button>
      <button data-f="regular">🚲 רגיל</button>
    </div>
    <label style="font-size:13px;color:var(--ink);display:flex;align-items:center;gap:6px">
      מעל
      <input id="chronic-days" type="number" value="3" min="1" max="365"
        style="width:54px;padding:5px 8px;border:1px solid var(--hairline);
               border-radius:var(--radius);font-size:13px;font-family:var(--font);
               background:var(--surface);color:var(--ink);text-align:center">
      ימים
    </label>
  </div>
  <div class="card" style="padding:16px 20px">
    <div class="info-btn">i<span class="tip">מציג תחנות שבX ימים רצופים האחרונים לא היו בהן אופניים (לפי הפילטר הנבחר). אם יום אחד היו בתחנה אופניים — הסטריק נשבר והיא לא תופיע.</span></div>
    <div class="rank-scroll" style="height:420px;overflow-y:auto">
      <div id="chronic-wrap" style="position:relative;min-height:100px">
        <canvas id="chart-chronic"></canvas>
      </div>
    </div>
    <div id="chronic-msg" style="display:none;text-align:center;padding:20px 0;color:var(--muted);font-size:14px">
      אין תחנות העומדות בקריטריון הנבחר
    </div>
  </div>
</section>

<!-- ══ 8. HISTOGRAM ══════════════════════════════════════════════════════════ -->
<section class="sec" id="histogram">
  <h2 class="sec-title">התפלגות תחנות לפי כמות אופניים</h2>
  <div class="toggle-group" id="hist-filter">
    <button class="active" data-f="all">הכל</button>
    <button data-f="electric">⚡ חשמלי</button>
    <button data-f="regular">🚲 רגיל</button>
  </div>
  <div class="row">
    <div class="card" style="flex:1;min-width:280px">
      <div class="info-btn">i<span class="tip">מראה כמה תחנות נמצאות בכל טווח של מספר אופניים זמינים. הנתון לכל תחנה הוא ממוצע האופניים הזמינים בה בשעות האחרונות.</span></div>
      <div class="card-title">התפלגות תחנות לפי באקטים</div>
      <div class="chart-wrap"><canvas id="hist-bucket" height="240"></canvas></div>
    </div>
    <div class="card" style="flex:1;min-width:280px">
      <div class="info-btn">i<span class="tip">לכל ערך X על ציר ה-X, הגרף מראה כמה אחוז מהתחנות יש בהן X אופניים או פחות. למשל, אם הנקודה על X=3 היא 40% — משמעות הדבר שב-40% מהתחנות יש 3 אופניים או פחות.</span></div>
      <div class="card-title">התפלגות מצטברת</div>
      <div class="chart-wrap"><canvas id="hist-cum" height="240"></canvas></div>
    </div>
  </div>
</section>

<!-- ══ 9. TREND AVG/MEDIAN ═══════════════════════════════════════════════════ -->
<section class="sec" id="trend-avg">
  <h2 class="sec-title">📉ממוצע/חציון אופניים לתחנה לאורך זמן</h2>
  <div class="card">
    <div class="info-btn">i<span class="tip">ממוצע וחציון של מספר האופניים הזמינים לתחנה ביום נתון. לכל תחנה מחושב ממוצע יומי (מכלל המדידות כל שעה), ואז לוקחים ממוצע וחציון על פני כל התחנות. החציון פחות מושפע מתחנות קיצוניות (ריקות/מלאות).</span></div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div class="card-title" style="margin-bottom:0"></div>
      <div class="toggle-group" id="gran-avg">
        <button class="active" data-g="daily">יומי</button>
        <button data-g="weekly">שבועי</button>
        <button data-g="monthly">חודשי</button>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="chart-trend-avg" height="280"></canvas></div>
  </div>
</section>

</main>

</div><!-- /.layout -->

<script>
const DATA = __DATA__;
const SVGS = __SVGS__;
Chart.register(ChartDataLabels);

// ── PostHog palette ───────────────────────────────────────────────────────────
const isMobile = window.innerWidth < 600;
const Y = '__YELLOW__';        // electric — PostHog primary #f7a501
const B = '__BLUE__';          // regular  — PostHog accent  #2c84e0
const INK   = '#23251d';
const MUTED = '#6c6e63';
const GREEN = '#2c8c66';
const RED   = '#cd4239';
const ASH   = '#9b9c92';

// ── stamp ──────────────────────────────────────────────────────────────────────
document.getElementById('stamp').textContent = 'עודכן ' + DATA.meta.generated_at;

// ── 1. KPIs ───────────────────────────────────────────────────────────────────
// DOM order is reversed from RTL display order (first in DOM = leftmost on screen)
// Display right→left: פעילות | חציון | ללא חשמליות | ריקות | תקולים% | gauge
const grid = document.getElementById('kpi-grid');

// Gauge card — leftmost in RTL = first in DOM
grid.insertAdjacentHTML('beforeend',
  `<div class="kpi g" style="display:flex;flex-direction:column;align-items:center">
     <div class="gauge-wrap" style="width:100%;position:relative">
       <div class="gauge-clip">
         <canvas id="gauge-elec"></canvas>
       </div>
       <div style="position:absolute;bottom:18px;left:0;right:0;text-align:center;pointer-events:none">
         <div class="v" id="gauge-total" style="font-size:28px;line-height:1"></div>
         <div class="l" style="font-size:11px;margin-top:2px">אופניים זמינים</div>
       </div>
       <div class="gauge-labels">
         <span style="color:var(--primary)" id="gauge-elec-pct"></span>
         <span style="color:var(--blue)"    id="gauge-reg-pct"></span>
       </div>
     </div>
   </div>`);

grid.insertAdjacentHTML('beforeend',
  `<div class="kpi gray">
     <div class="v">${DATA.kpis.pct_disabled}%</div>
     <div class="l">אופניים תקולים</div>
     <div style="font-size:11px;color:var(--muted);margin-top:2px;line-height:1.3">(${DATA.kpis.disabled}/${DATA.kpis.total_bikes})</div>
     <div style="font-size:22px;margin-top:6px;line-height:1">🔧</div>
   </div>`);

[
  {v: DATA.kpis.empty_stations,       l:'תחנות ריקות',         cls:'r', icon:'🚳'},
  {v: DATA.kpis.no_electric_stations, l:'תחנות ללא חשמליות',   cls:'y', icon:'⚡'},
  {v: DATA.kpis.median_per_station,   l:'חציון אופניים לתחנה', cls:'g', icon:'🚲'},
  {v: DATA.kpis.active_stations,      l:'תחנות פעילות',        cls:'b', icon:'✅'},
].forEach(k => {
  grid.insertAdjacentHTML('beforeend',
    `<div class="kpi ${k.cls}">
       <div class="v">${k.v}</div>
       <div class="l">${k.l}</div>
       <div style="font-size:22px;margin-top:8px;line-height:1">${k.icon}</div>
     </div>`);
});

// ── gauge: % electric of total available ──────────────────────────────────
(function(){
  const pct = DATA.kpis.pct_electric;
  const reg = 100 - pct;
  document.getElementById('gauge-total').textContent    = DATA.kpis.total_available;
  document.getElementById('gauge-elec-pct').textContent = '⚡ ' + pct + '%';
  document.getElementById('gauge-reg-pct').textContent  = '🚲 ' + reg + '%';
  new Chart(document.getElementById('gauge-elec'), {
    type: 'doughnut',
    data: {
      datasets: [{
        data: [pct, 100 - pct],
        backgroundColor: [Y, B],
        borderWidth: 0,
        hoverOffset: 0,
      }]
    },
    options: {
      rotation: -90,
      circumference: 180,
      cutout: '68%',
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 2,
      layout: {padding: 0},
      plugins: {
        legend:     {display: false},
        datalabels: {display: false},
        tooltip:    {enabled: false},
      },
      animation: {animateRotate: true, duration: 900},
    }
  });
})();

// ── 2. HISTOGRAM ──────────────────────────────────────────────────────────────
let histBucketChart = null, histCumChart = null;
let activeFilter = 'all';

// PostHog-palette bucket colors: red→amber→green
const BUCKET_COLORS = [RED,'#b36200','#c8940a','#c8940a',GREEN,GREEN,GREEN];

function buildHistCharts(filter) {
  const h = DATA.histogram[filter];
  const total = DATA.kpis.active_stations;

  // bucket chart
  if (histBucketChart) histBucketChart.destroy();
  histBucketChart = new Chart(document.getElementById('hist-bucket'), {
    type: 'bar',
    data: {
      labels: h.labels,
      datasets: [{data: h.counts, backgroundColor: BUCKET_COLORS, borderRadius:4}]
    },
    options: {
      responsive:true, layout:{padding:{top:48}},
      plugins:{
        legend:{display:false},
        datalabels:{
          anchor:'end', align:'top', clamp:true,
          textAlign:'center',
          color:'#333', font:{weight:'bold',size:12},
          formatter: v => {
            if (!v) return '';
            return '‪'+Math.round(v/total*100)+'% ('+v+')‬';
          }
        },
        tooltip:{rtl:true, bodyAlign:'right', titleAlign:'right', callbacks:{
          title: () => '',
          label: ctx => {
            const v = ctx.parsed.y;
            return [v + ' תחנות', Math.round(v/total*100) + '% מכלל התחנות'];
          }
        }}
      },
      scales:{
        y:{beginAtZero:true,grid:{color:'#eee'},ticks:{font:{size:10},color:MUTED},border:{display:true},title:{display:true,text:'מספר תחנות',color:MUTED,font:{size:11}}},
        x:{grid:{display:false},title:{display:true,text:'כמות אופניים',color:MUTED,font:{size:11}}}
      }
    }
  });

  // cumulative line chart — % stations with AT MOST i bikes
  const cumLabels = h.cumulative.map((_,i) => '≤'+i);
  if (histCumChart) histCumChart.destroy();
  histCumChart = new Chart(document.getElementById('hist-cum'), {
    type: 'line',
    data: {
      labels: cumLabels,
      datasets:[{
        label:'% תחנות עם לכל היותר X אופניים',
        data: h.cumulative,
        borderColor: filter==='electric'?Y:filter==='regular'?B:'#27ae60',
        backgroundColor: filter==='electric'?'rgba(240,180,41,.15)':filter==='regular'?'rgba(41,128,185,.15)':'rgba(39,174,96,.15)',
        fill:true, tension:0.3, pointRadius:4,
      }]
    },
    options:{
      responsive:true, layout:{padding:{top:10,right:20}},
      clip: false,
      plugins:{
        legend:{display:false},
        datalabels:{display:false},
        tooltip:{callbacks:{
          title: ctx => 'לכל היותר ' + ctx[0].label.replace('≤','') + ' אופניים',
          label: ctx => ctx.parsed.y + '% מהתחנות',
        }}
      },
      scales:{
        y:{
          beginAtZero:true, max:100,
          grid:{color:'#eee'},
          title:{display:true,text:'% תחנות',color:MUTED,font:{size:11}},
          ticks:{callback: v => v+'%', font:{size:10}, color:MUTED},
          border:{display:true}
        },
        x:{
          grid:{display:false},
          title:{display:true,text:'מקסימום אופניים בתחנה',color:MUTED,font:{size:11}},
          ticks:{maxRotation:0, minRotation:0}
        }
      }
    }
  });
}

buildHistCharts('all');

document.getElementById('hist-filter').querySelectorAll('button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#hist-filter button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    buildHistCharts(btn.dataset.f);
  });
});

// ── 3. RANKING ────────────────────────────────────────────────────────────────
let rankChart = null;
let rankSortDir = 'desc';
function buildRankChart(filter) {
  const sta = [...DATA.stations_geo];
  const dir = rankSortDir === 'asc' ? 1 : -1;
  let datasets;
  if (filter === 'electric') {
    sta.sort((a,b) => dir * (a.electric - b.electric));
    datasets = [{label:'⚡ חשמלי', data:sta.map(s=>s.electric), backgroundColor:Y, borderRadius:2}];
  } else if (filter === 'regular') {
    sta.sort((a,b) => dir * (a.regular - b.regular));
    datasets = [{label:'🚲 רגיל', data:sta.map(s=>s.regular), backgroundColor:B, borderRadius:2}];
  } else {
    sta.sort((a,b) => dir * (a.available - b.available));
    datasets = [
      {label:'⚡ חשמלי', data:sta.map(s=>s.electric), backgroundColor:Y, borderRadius:2},
      {label:'🚲 רגיל',  data:sta.map(s=>s.regular),  backgroundColor:B, borderRadius:2},
    ];
  }
  const labels = sta.map(s=>s.name);
  if (rankChart) rankChart.destroy();
  rankChart = new Chart(document.getElementById('rank-chart'), {
    type: 'bar',
    data: {labels, datasets},
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      layout: {padding:{right:70}},
      plugins: {
        legend: {display:true, position:'bottom',
          labels:{font:{size:13,family:"'IBM Plex Sans',sans-serif"},
                  color:INK, boxWidth:14, padding:20}},
        datalabels: {
          display: ctx => ctx.datasetIndex === datasets.length - 1,
          anchor: 'end', align: 'right', offset: 8,
          color: INK, font: {size:11, weight:'600'},
          formatter: (v, ctx) => {
            const s = sta[ctx.dataIndex];
            return filter === 'electric' ? s.electric
                 : filter === 'regular'  ? s.regular
                 : s.available;
          }
        },
        tooltip: {
          rtl:true, bodyAlign:'right', titleAlign:'right',
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => {
              const s = sta[ctx.dataIndex];
              if (filter==='all') return [
                '⚡ חשמלי: '+s.electric,
                '🚲 רגיל: '+s.regular,
                'סה״כ: '+s.available,
              ];
              return ctx.dataset.label+': '+ctx.parsed.x;
            }
          }
        }
      },
      scales: {
        x: {stacked:true, beginAtZero:true, grid:{color:'#eee'},
            ticks:{font:{size:11}}},
        y: {stacked:true, ticks:{font:{size:11}, color:INK}}
      }
    }
  });
}
buildRankChart('all');
document.getElementById('rank-filter').querySelectorAll('button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#rank-filter button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    buildRankChart(btn.dataset.f);
  });
});
document.getElementById('rank-sort').querySelectorAll('button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#rank-sort button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    rankSortDir = btn.dataset.s;
    const activeFilter = document.querySelector('#rank-filter button.active').dataset.f;
    buildRankChart(activeFilter);
  });
});

// ── 4. MAP ────────────────────────────────────────────────────────────────────
const SHABBAT = new Set(DATA.shabbat_stations);
function initMap() {
  const gmap = new google.maps.Map(document.getElementById('map-el'), {
    center: {lat: 31.782, lng: 35.218},
    zoom: 13,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true,
  });

  const iw = new google.maps.InfoWindow();
  window._closeIW = () => iw.close();

  function makeIcon(available, disabled, shabbat) {
    const color = available > 0 ? '#27ae60' : (disabled > 0 ? '#9b9c92' : '#cd4239');
    const fs    = available >= 10 ? 9 : 11;
    const ring  = shabbat ? '<circle cx="15" cy="14" r="10" fill="none" stroke="#f0b429" stroke-width="2.5"/>' : '';
    const star  = shabbat ? '<text x="26" y="7" text-anchor="middle" font-size="10" font-family="Arial,sans-serif">✡</text>' : '';
    const svg   = `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="38" viewBox="0 0 30 38">
      <path d="M15 0C6.716 0 0 6.716 0 15c0 8.284 15 23 15 23S30 23.284 30 15C30 6.716 23.284 0 15 0z" fill="${color}"/>
      <circle cx="15" cy="14" r="10" fill="white"/>
      ${ring}${star}
      <text x="15" y="18.5" text-anchor="middle" fill="${color}" font-size="${fs}" font-weight="bold" font-family="Arial,sans-serif">${available}</text>
    </svg>`;
    return {
      url:        'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(svg),
      scaledSize: new google.maps.Size(30, 38),
      anchor:     new google.maps.Point(15, 38),
    };
  }

  const markerMap = {};
  DATA.stations_geo.forEach(s => {
    if (!s.lat || !s.lng) return;
    const marker = new google.maps.Marker({
      position: {lat: s.lat, lng: s.lng},
      map:      gmap,
      icon:     makeIcon(s.available, s.disabled, SHABBAT.has(s.name)),
      title:    s.name,
    });
    markerMap[s.name] = {marker, s};
    function openInfo() {
      iw.setContent(`
        <div style="direction:rtl;min-width:160px;font-family:'IBM Plex Sans',Arial,sans-serif;position:relative">
          <span onclick="_closeIW()" style="position:absolute;top:-2px;left:-2px;cursor:pointer;font-size:13px;color:#aaa;line-height:1;padding:2px 5px">✕</span>
          <strong style="font-size:13px">${s.name}</strong><br>
          ${s.address ? '<span style="color:#888;font-size:11px">'+s.address+'</span><br>' : ''}
          ${SHABBAT.has(s.name) ? '<span style="color:#f0b429;font-size:11px">✡ פעילה בשבת לחשמליות</span><br>' : ''}
          <span style="color:#f0b429">⚡ חשמלי: ${s.electric}</span><br>
          <span style="color:#2980b9">🚲 רגיל: ${s.regular}</span><br>
          <span>🔧 תקולים: ${s.disabled}</span><br>
          <span>🅿️ עגינות: ${s.docks}</span>
        </div>`);
      iw.open(gmap, marker);
      document.getElementById('sta-search').value = s.name;
      renderStation(s.name, activeStaGran);
    }
    marker.addListener('click', openInfo);
    markerMap[s.name].openInfo = openInfo;
  });

  // ── map search ──
  const searchInput = document.getElementById('map-search');
  const suggestions = document.getElementById('map-suggestions');
  const names = DATA.stations_geo.map(s => s.name);

  function hideSuggestions() { suggestions.style.display = 'none'; }

  searchInput.addEventListener('input', () => {
    const q = searchInput.value.trim();
    suggestions.innerHTML = '';
    if (!q) { hideSuggestions(); return; }
    const matches = names.filter(n => n.includes(q)).slice(0, 8);
    if (!matches.length) { hideSuggestions(); return; }
    matches.forEach(name => {
      const li = document.createElement('li');
      li.textContent = name;
      li.style.cssText = 'padding:8px 14px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--hairline-soft)';
      li.addEventListener('mouseenter', () => li.style.background = 'var(--surface-soft)');
      li.addEventListener('mouseleave', () => li.style.background = '');
      li.addEventListener('click', () => {
        searchInput.value = name;
        hideSuggestions();
        const entry = markerMap[name];
        if (entry) {
          gmap.panTo(entry.marker.getPosition());
          gmap.setZoom(16);
          entry.openInfo();
        }
      });
      suggestions.appendChild(li);
    });
    suggestions.style.display = 'block';
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('#map-search') && !e.target.closest('#map-suggestions'))
      hideSuggestions();
  });
}

// ── rollup helper ──────────────────────────────────────────────────────────────
function rollup(rows, granularity, fields) {
  if (granularity === 'daily' || !rows.length) return rows;
  const groups = {};
  rows.forEach(r => {
    const d = r.date;
    let key;
    if (granularity === 'weekly') {
      const dt = new Date(d);
      // ISO week key: YYYY-Www
      dt.setDate(dt.getDate() - ((dt.getDay()+6)%7)); // Monday
      key = dt.toISOString().slice(0,10);
    } else {
      key = d.slice(0,7); // YYYY-MM
    }
    if (!groups[key]) groups[key] = [];
    groups[key].push(r);
  });
  return Object.entries(groups)
    .sort(([a],[b]) => a.localeCompare(b))
    .map(([key, grp]) => {
      const out = {date: key};
      fields.forEach(f => {
        const nums = grp.map(r => r[f]).filter(v => v != null);
        out[f] = nums.length ? Math.round(nums.reduce((a,b)=>a+b,0)/nums.length * 10)/10 : null;
      });
      return out;
    });
}

// ── 4. TREND AVG/MEDIAN ───────────────────────────────────────────────────────
let trendAvgChart = null;
function renderTrendAvg(gran) {
  const rows = rollup(DATA.daily_network, gran,
    ['avg_available','median_available']);
  const labels = rows.map(r => r.date);
  if (trendAvgChart) trendAvgChart.destroy();
  trendAvgChart = new Chart(document.getElementById('chart-trend-avg'), {
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'ממוצע', data:rows.map(r=>r.avg_available),
         borderColor:GREEN,backgroundColor:'rgba(44,140,102,.12)',
         fill:true,tension:0.3,pointRadius:2},
        {label:'חציון', data:rows.map(r=>r.median_available),
         borderColor:MUTED,backgroundColor:'transparent',
         borderDash:[5,3],tension:0.3,pointRadius:2},
      ]
    },
    options: trendOpts('ממוצע אופניים לתחנה')
  });
}

// ── 5. TREND EMPTY ────────────────────────────────────────────────────────────
let trendEmptyChart = null;
// precompute empty stations per date from daily_stations
const emptyByDate = {};
const noElecByDate = {};
Object.entries(DATA.daily_stations).forEach(([name, rows]) => {
  rows.forEach(r => {
    if ((r.elec + r.reg) < 0.5) {
      if (!emptyByDate[r.date]) emptyByDate[r.date] = [];
      emptyByDate[r.date].push(name);
    }
    if (r.elec < 0.5) {
      if (!noElecByDate[r.date]) noElecByDate[r.date] = [];
      noElecByDate[r.date].push(name);
    }
  });
});

function renderTrendEmpty(gran) {
  const rows = rollup(DATA.daily_network, gran,
    ['empty_stations','no_electric_stations']);
  const labels = rows.map(r => r.date);
  const total = DATA.kpis.active_stations || 1;
  const toPct = v => v == null ? null : Math.round(v / total * 1000) / 10;
  if (trendEmptyChart) trendEmptyChart.destroy();
  const base = trendOpts('% תחנות');
  base.scales.y.ticks = {...base.scales.y.ticks, callback: v => v + '%'};
  base.plugins.datalabels.formatter = v => v == null ? '' : v.toFixed(1) + '%';
  base.interaction = {mode:'nearest', intersect:false};
  base.plugins.tooltip = {
    rtl: true, bodyAlign: 'right', titleAlign: 'right',
    callbacks: {
      label: ctx => {
        const abs = ctx.datasetIndex === 0
          ? rows[ctx.dataIndex].empty_stations
          : rows[ctx.dataIndex].no_electric_stations;
        return ` ${ctx.parsed.y.toFixed(1)}% (${abs} תחנות)`;
      },
      afterBody: ctx => {
        const date = labels[ctx[0].dataIndex];
        const dsIndex = ctx[0].datasetIndex;
        const names = dsIndex === 0 ? emptyByDate[date] : noElecByDate[date];
        if (!names || !names.length) return [];
        const label = dsIndex === 0 ? '🔴 תחנות ריקות:' : '⚡ ללא חשמליות:';
        return ['', label, ...names.map(n => '  • ' + n)];
      }
    }
  };
  trendEmptyChart = new Chart(document.getElementById('chart-trend-empty'), {
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'ללא אופניים כלל',
         data:rows.map(r=>toPct(r.empty_stations)),
         borderColor:RED,backgroundColor:'rgba(205,66,57,.1)',
         fill:true,tension:0.3,pointRadius:2},
        {label:'ללא חשמליות',
         data:rows.map(r=>toPct(r.no_electric_stations)),
         borderColor:Y,backgroundColor:'rgba(240,180,41,.1)',
         fill:true,tension:0.3,pointRadius:2},
      ]
    },
    options: base
  });
}

// ── 5.5. CHRONIC EMPTY ────────────────────────────────────────────────────────
let chronicChart = null;
let chronicFilter = 'all';
let chronicDays = 3;

function renderChronic() {
  const COLOR = {electric:'rgba(240,180,41,.75)', regular:'rgba(41,128,185,.75)', all:'rgba(205,66,57,.75)'};
  const LABEL = {electric:'ימים ללא חשמליות', regular:'ימים ללא רגילות', all:'ימים ללא אופניים כלל'};

  // compute consecutive-day streak ending at the most recent date
  const counts = {};
  Object.entries(DATA.daily_stations).forEach(([name, rows]) => {
    const sorted = [...rows].sort((a,b) => a.date < b.date ? 1 : -1); // newest first
    let streak = 0;
    for (const r of sorted) {
      const empty = chronicFilter === 'electric' ? r.elec < 0.5
                  : chronicFilter === 'regular'  ? r.reg  < 0.5
                  : (r.elec + r.reg) < 0.5;
      if (empty) streak++;
      else break;
    }
    if (streak > chronicDays) counts[name] = streak;
  });

  const wrap = document.getElementById('chronic-wrap');
  const msg  = document.getElementById('chronic-msg');
  if (chronicChart) { chronicChart.destroy(); chronicChart = null; }

  const sorted = Object.entries(counts).sort((a,b) => b[1] - a[1]);
  if (!sorted.length) {
    wrap.style.display = 'none'; msg.style.display = 'block'; return;
  }
  wrap.style.display = 'block'; msg.style.display = 'none';
  wrap.style.height = Math.max(120, sorted.length * 32) + 'px';

  chronicChart = new Chart(document.getElementById('chart-chronic'), {
    type:'bar',
    data:{
      labels: sorted.map(([n]) => n),
      datasets:[{
        data: sorted.map(([,v]) => v),
        backgroundColor: COLOR[chronicFilter],
        borderRadius: 4
      }]
    },
    options:{
      indexAxis:'y',
      responsive:true,
      maintainAspectRatio:false,
      plugins:{
        legend:{display:false},
        datalabels:{
          display:true, anchor:'end', align:'right', offset:4,
          font:{size:11,family:"'IBM Plex Sans',sans-serif"}, color:MUTED,
          formatter: v => v + ' ימים'
        },
        tooltip:{
          rtl:true, bodyAlign:'right', titleAlign:'right',
          callbacks:{
            label: ctx => ctx.parsed.x + ' ' + LABEL[chronicFilter]
          }
        }
      },
      scales:{
        y:{ticks:{color:INK,font:{size:12,family:"'IBM Plex Sans',sans-serif"}}, grid:{display:false}},
        x:{beginAtZero:true, ticks:{color:MUTED,font:{size:11},stepSize:1},
           title:{display:!isMobile,text:'מספר ימים',color:MUTED,font:{size:11}},
           grid:{color:'#eee'}}
      },
      layout:{padding:{right:80}}
    }
  });
}

document.getElementById('chronic-filter').querySelectorAll('button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.getElementById('chronic-filter').querySelectorAll('button')
      .forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    chronicFilter = btn.dataset.f;
    renderChronic();
  });
});
document.getElementById('chronic-days').addEventListener('change', e => {
  chronicDays = Math.max(0, parseInt(e.target.value) || 0);
  renderChronic();
});
renderChronic();

// ── 6. TREND DISABLED ─────────────────────────────────────────────────────────
let trendDisChart = null;
function renderTrendDis(gran) {
  const rows = rollup(DATA.daily_network, gran, ['total_disabled']);
  const labels = rows.map(r => r.date);
  const total = DATA.kpis.total_bikes || 1;
  const toPct = v => v == null ? null : Math.round(v / total * 1000) / 10;
  if (trendDisChart) trendDisChart.destroy();
  const base = trendOpts('% אופניים תקולים');
  base.scales.y.ticks = {...base.scales.y.ticks, callback: v => v + '%'};
  base.plugins.datalabels.formatter = v => v == null ? '' : v.toFixed(1) + '%';
  base.plugins.tooltip = {
    rtl: true, bodyAlign: 'right', titleAlign: 'right',
    callbacks: {
      label: ctx => {
        const abs = Math.round(rows[ctx.dataIndex].total_disabled);
        return ` ${ctx.parsed.y.toFixed(1)}% (${abs} אופניים תקולים)`;
      }
    }
  };
  trendDisChart = new Chart(document.getElementById('chart-trend-dis'), {
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'תקולים סה״כ',
         data:rows.map(r=>toPct(r.total_disabled)),
         borderColor:ASH,backgroundColor:'rgba(155,156,146,.12)',
         fill:true,tension:0.3,pointRadius:2},
      ]
    },
    options: base
  });
}

function trendOpts(yLabel) {
  return {
    responsive:true,
    interaction:{mode:'index',intersect:false},
    plugins:{
      legend:{rtl:true, position:'bottom',labels:{font:{size:13,family:"'IBM Plex Sans',sans-serif"},
        color:MUTED, boxWidth:12}},
      datalabels:{
        display:true,
        anchor:'end', align:'top', offset:2,
        clamp:true,
        font:{size:10, family:"'IBM Plex Sans',sans-serif"},
        color:MUTED,
        formatter: v => v == null ? '' : (Number.isInteger(v) ? v : v.toFixed(1))
      },
    },
    scales:{
      y:{beginAtZero:true,
         grid:{color:getComputedStyle(document.documentElement)
           .getPropertyValue('--hairline-soft').trim()||'#dcdfd2'},
         ticks:{color:MUTED,font:{size: isMobile ? 9 : 11}},
         border:{display:true},
         title:{display:true,text:yLabel,color:MUTED,font:{size: isMobile ? 9 : 11}}},
      x:{type:'category',grid:{color:'#eeefe9'},
         ticks:{maxTicksLimit:18,color:MUTED,font:{size:10},
           callback: function(val){
             const lbl=this.getLabelForValue(val);
             if(!lbl) return '';
             const p=lbl.split('-');
             return p.length===3 ? parseInt(p[2])+'/'+parseInt(p[1]) : lbl;
           }}}
    },
    layout:{autoPadding:false,padding:{top:18,left:0,right:0}}
  };
}

// ── gran toggles for trend charts ──────────────────────────────────────────────
function setupGranToggle(groupId, renderFn) {
  document.getElementById(groupId).querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#'+groupId+' button').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      renderFn(btn.dataset.g);
    });
  });
}

renderTrendAvg('daily');
renderTrendEmpty('daily');
renderTrendDis('daily');
setupGranToggle('gran-avg',   renderTrendAvg);
setupGranToggle('gran-empty', renderTrendEmpty);
setupGranToggle('gran-dis',   renderTrendDis);

// ── 7. STATION DETAIL ─────────────────────────────────────────────────────────
const staSearch = document.getElementById('sta-search');
const staSugg   = document.getElementById('sta-suggestions');

function renderStaSugg(q) {
  staSugg.innerHTML = '';
  const matches = q ? DATA.station_names.filter(n => n.includes(q)) : DATA.station_names;
  if (!matches.length) { staSugg.style.display = 'none'; return; }
  matches.forEach(name => {
    const li = document.createElement('li');
    li.textContent = name;
    li.addEventListener('mousedown', e => e.preventDefault());
    li.addEventListener('touchend', e => { e.preventDefault(); pickStation(name); });
    li.addEventListener('click', () => pickStation(name));
    staSugg.appendChild(li);
  });
  staSugg.style.display = 'block';
}

function pickStation(name) {
  staSearch.value = name;
  staSugg.style.display = 'none';
  renderStation(name, activeStaGran);
}

staSearch.addEventListener('focus', () => renderStaSugg(staSearch.value.trim()));
staSearch.addEventListener('input', () => renderStaSugg(staSearch.value.trim()));
document.addEventListener('click', e => {
  if (!e.target.closest('#sta-sel-wrap')) staSugg.style.display = 'none';
});

let staChart = null;
function renderStation(name, gran) {
  let rows, labels;
  if (gran === 'hourly') {
    rows   = DATA.hourly_stations[name] || [];
    labels = rows.map(r => r.hour);
  } else {
    const raw = DATA.daily_stations[name] || [];
    rows   = rollup(raw, gran, ['elec','reg']);
    labels = rows.map(r => r.date);
  }
  if (staChart) staChart.destroy();
  staChart = new Chart(document.getElementById('chart-sta'), {
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'⚡ חשמלי', data:rows.map(r=>r.elec),
         borderColor:Y,backgroundColor:'rgba(240,180,41,.15)',
         fill:true,tension:0.3,pointRadius:2},
        {label:'🚲 רגיל',  data:rows.map(r=>r.reg),
         borderColor:B,backgroundColor:'rgba(41,128,185,.12)',
         fill:true,tension:0.3,pointRadius:2},
      ]
    },
    options:{
      responsive:true,
      interaction:{mode:'index',intersect:false},
      plugins:{
        legend:{position:'bottom',labels:{font:{size:12}}},
        datalabels:{
          display:true,
          anchor:'end', align:'top', offset:2, clamp:true,
          font:{size:10, family:"'IBM Plex Sans',sans-serif"},
          color:MUTED,
          formatter: v => v == null ? '' : (Number.isInteger(v) ? v : v.toFixed(1))
        },
        title:{display:true, text:name, font:{size:13,weight:'bold'}, padding:{bottom:8}}
      },
      scales:{
        y:{beginAtZero:true,grid:{color:'#eee'},ticks:{color:MUTED,font:{size: isMobile ? 9 : 11}},border:{display:true},title:{display:true,text:'כמות אופניים',color:MUTED,font:{size: isMobile ? 9 : 11}}},
        x:{type:'category',grid:{color:'#f0f0f0'},ticks:{maxTicksLimit:18,font:{size:10},
          callback: function(val){
            const lbl=this.getLabelForValue(val);
            if(!lbl) return '';
            if(lbl.includes('T')){
              const [d,t]=lbl.split('T');
              const p=d.split('-');
              return parseInt(p[2])+'/'+parseInt(p[1])+' '+t;
            }
            const p=lbl.split('-');
            return p.length===3 ? parseInt(p[2])+'/'+parseInt(p[1]) : lbl;
          }}}
      },
      layout:{autoPadding:false,padding:{top:18,left:0,right:0}}
    }
  });
}

let activeStaGran = 'daily';
setupGranToggle('gran-sta', g => { activeStaGran = g; if (staSearch.value) renderStation(staSearch.value, g); });
if (DATA.station_names.length) {
  staSearch.value = DATA.station_names[0];
  renderStation(DATA.station_names[0], 'daily');
}

// ── TOC scroll spy + smooth click ─────────────────────────────────────────────
const secs = Array.from(document.querySelectorAll('section[id]'));
const tocAs = document.querySelectorAll('.toc a');
const HDR = 68; // sticky header height + small gap

function setActiveToc(id) {
  tocAs.forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + id));
}

// scroll spy: whichever section's top is closest to (but still above) HDR wins
window.addEventListener('scroll', () => {
  let current = secs[0].id;
  for (const s of secs) {
    if (s.getBoundingClientRect().top <= HDR) current = s.id;
  }
  setActiveToc(current);
}, {passive:true});
setActiveToc(secs[0].id); // initial state

// click: scroll so section h2 lands at top of viewport
tocAs.forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    const target = document.querySelector(a.getAttribute('href'));
    if (!target) return;
    const h2 = target.querySelector('h2.sec-title') || target;
    const top = h2.getBoundingClientRect().top + window.scrollY - HDR;
    window.scrollTo({top, behavior:'smooth'});
    history.pushState(null, '', a.getAttribute('href'));
  });
});
</script>
</body>
</html>
"""


# ── entry point ────────────────────────────────────────────────────────────────

def generate(output: str = OUTPUT_FILE):
    data = build_data()
    svgs = _read_svgs()
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__SVGS__", json.dumps(svgs, ensure_ascii=False))
    html = html.replace("__YELLOW__", YELLOW).replace("__BLUE__", BLUE)
    html = html.replace("__MAPS_KEY__", GOOGLE_MAPS_KEY)
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard written → {output}")
    print(f"  Stations : {data['meta']['total_stations']}")
    print(f"  Daily rows: {len(data['daily_network'])}")
    print(f"  Generated: {data['meta']['generated_at']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()
    generate(args.output)
