"""
JeruFun Station Monitor — Streamlit Dashboard (v2)
Run: streamlit run dashboard.py
"""

import threading
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components

import collector
import db
from config import (
    BLACKLIST_STATIONS, MAP_URL, BIKE_TYPES,
    POLL_INTERVAL_MINUTES, VAN_JUMP_THRESHOLD,
)

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ירופאן מוניטור",
    page_icon="🚲",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── RTL CSS ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  body, .stApp { direction: rtl; }
  .main .block-container { direction: rtl; text-align: right; padding-top: 1rem; }
  .stMetric { direction: rtl; text-align: right; }
  .stMetric label { text-align: right !important; }
  .stSelectbox label, .stMultiSelect label,
  .stRadio label, .stSlider label { direction: rtl; }
  div[data-testid="stMetricValue"] { direction: rtl; }
  /* hide collapsed sidebar toggle */
  [data-testid="collapsedControl"] { display: none; }
  [data-testid="stSidebar"] { display: none; }
  /* nav panel styling */
  .nav-panel {
      background: #f8fafc;
      border-radius: 12px;
      padding: 1.2rem 1rem;
      border: 1px solid #e2e8f0;
      position: sticky;
      top: 0.5rem;
      min-height: 80vh;
  }
  .nav-title { font-size: 1.1rem; font-weight: 700; color: #1e293b; margin-bottom: 0.5rem; }
  div[data-testid="stRadio"] label { font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# ── background collector ──────────────────────────────────────────────────────
def _bg_collect():
    db.init_db()
    while True:
        try:
            collector.collect_once()
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_MINUTES * 60)

if "collector_started" not in st.session_state:
    threading.Thread(target=_bg_collect, daemon=True).start()
    st.session_state["collector_started"] = True

# ── session-state navigation ──────────────────────────────────────────────────
PAGES = ["🗺️ מפת הרשת", "📊 סקירה כללית", "🏆 לידרבורד מחסור", "🔍 תחנה ספציפית"]

if "page_idx" not in st.session_state:
    st.session_state["page_idx"] = 0
if "selected_station" not in st.session_state:
    st.session_state["selected_station"] = None

if st.session_state.get("go_to_station"):
    st.session_state["selected_station"] = st.session_state.pop("go_to_station")
    st.session_state["page_idx"] = 3  # תחנה ספציפית

# ── helpers ───────────────────────────────────────────────────────────────────
DOW_LABELS = {0: "ראשון", 1: "שני", 2: "שלישי", 3: "רביעי",
               4: "חמישי", 5: "שישי", 6: "שבת"}

BUCKET_LABELS = ["0", "1–2", "3–4", "5–6", "7–8", "9–10", "11+"]
BUCKET_COLORS = ["#ef4444", "#f97316", "#f59e0b", "#84cc16", "#22c55e", "#16a34a", "#15803d"]

def assign_bucket(avg):
    if avg == 0:     return "0"
    elif avg <= 2:   return "1–2"
    elif avg <= 4:   return "3–4"
    elif avg <= 6:   return "5–6"
    elif avg <= 8:   return "7–8"
    elif avg <= 10:  return "9–10"
    else:            return "11+"

def navigate_to(station_name: str):
    st.session_state["go_to_station"] = station_name
    st.rerun()

def fetch_live_map():
    try:
        r = requests.get(MAP_URL, timeout=10)
        r.raise_for_status()
        return [s for s in r.json().get("stations", [])
                if s.get("name") not in BLACKLIST_STATIONS]
    except Exception:
        return []

def avail_color(n):
    if n == 0:   return "#ef4444"
    if n <= 2:   return "#f59e0b"
    return "#22c55e"

# ── layout: content | nav ─────────────────────────────────────────────────────
n_rows = db.count_snapshots()
_, max_ts = db.get_collection_range()

content_col, nav_col = st.columns([5, 1], gap="large")

with nav_col:
    st.markdown('<div class="nav-panel">', unsafe_allow_html=True)
    st.markdown('<div class="nav-title">🚲 ירופאן מוניטור</div>', unsafe_allow_html=True)
    st.markdown("---")

    page_label = st.radio(
        "ניווט",
        PAGES,
        index=st.session_state["page_idx"],
        label_visibility="collapsed",
    )
    st.session_state["page_idx"] = PAGES.index(page_label)

    st.markdown("---")

    timeframe_opts = {"24 שעות": 24, "7 ימים": 168, "30 ימים": 720}
    tf_label = st.selectbox("טווח זמן", list(timeframe_opts.keys()))
    tf_hours = timeframe_opts[tf_label]

    st.markdown("---")
    st.caption(f"שורות: {n_rows:,}")
    if max_ts:
        st.caption(f"עדכון: {max_ts[11:16]}")

    st.markdown('</div>', unsafe_allow_html=True)

page = page_label

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — MAP
# ══════════════════════════════════════════════════════════════════════════════
with content_col:
  if page == "🗺️ מפת הרשת":
    st.title("🗺️ מפת הרשת — מצב בזמן אמת")

    stations = fetch_live_map()
    if not stations:
        st.warning("לא ניתן לטעון נתוני מפה כרגע.")
        st.stop()

    latest = db.get_latest_snapshot()
    detail_map = {}
    if not latest.empty:
        latest["bikes_available"] = latest["bikes_regular"] + latest["bikes_electric"]
        for _, row in latest.iterrows():
            detail_map[row["station_name"]] = row

    rows = []
    for s in stations:
        loc = s.get("location", {})
        lat, lng = loc.get("lat"), loc.get("lng")
        if not lat or not lng:
            continue
        name  = s.get("name", "")
        d     = detail_map.get(name, {})
        avail    = int(d.get("bikes_available", s.get("omniBikes", 0)))
        regular  = int(d.get("bikes_regular", 0))
        electric = int(d.get("bikes_electric", 0))
        disabled = int(d.get("bikes_disabled", 0))
        docks    = int(d.get("docks_free", 0))
        rows.append(dict(name=name, lat=lat, lng=lng,
                         avail=avail, regular=regular, electric=electric,
                         disabled=disabled, docks=docks,
                         color=avail_color(avail),
                         status="ריקה" if avail==0 else ("מועטה" if avail<=2 else "זמינה")))

    df_map = pd.DataFrame(rows)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚲 אופניים זמינים", int(df_map["avail"].sum()))
    c2.metric("⚡ חשמליים", int(df_map["electric"].sum()))
    c3.metric("🔴 תחנות ריקות", int((df_map["avail"]==0).sum()))
    c4.metric("🟡 מועטות (1-2)", int(df_map["avail"].between(1,2).sum()))

    st.markdown("---")
    st.caption("לחץ על תחנה כדי לראות גרף מפורט שלה")

    fig_map = px.scatter_mapbox(
        df_map, lat="lat", lon="lng",
        color="status",
        color_discrete_map={"ריקה":"#ef4444","מועטה":"#f59e0b","זמינה":"#22c55e"},
        size="avail", size_max=22,
        hover_name="name",
        hover_data={"lat":False,"lng":False,"status":False,"avail":False,
                    "regular":True,"electric":True,"disabled":True,"docks":True},
        labels={"regular":"רגיל","electric":"חשמלי","disabled":"מושבת","docks":"עגינות"},
        mapbox_style="open-street-map",
        zoom=12, center={"lat":31.779,"lon":35.214},
        height=560,
    )
    fig_map.update_layout(
        margin={"r":0,"t":0,"l":0,"b":0},
        legend=dict(title="זמינות", x=0.01, y=0.99,
                    bgcolor="rgba(255,255,255,0.85)", bordercolor="#ccc", borderwidth=1),
    )

    sel = st.plotly_chart(fig_map, use_container_width=True, on_select="rerun", key="map_chart")
    if sel and sel.get("selection",{}).get("points"):
        pt = sel["selection"]["points"][0]
        name = pt.get("hovertext") or (pt.get("customdata") or [None])[0]
        if name:
            navigate_to(name)

    st.markdown("""
    <div style='font-size:0.85rem;color:#666;margin-top:6px'>
      🟢 <b>זמינה</b> — 3+ אופניים &nbsp;
      🟡 <b>מועטה</b> — 1-2 אופניים &nbsp;
      🔴 <b>ריקה</b> — 0 אופניים
    </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
  elif page == "📊 סקירה כללית":
    st.title("📊 סקירה כללית")

    latest = db.get_latest_snapshot()
    if latest.empty:
        st.warning("אין נתונים עדיין. ממתין לאיסוף ראשון.")
        st.stop()

    latest["bikes_available"] = latest["bikes_regular"] + latest["bikes_electric"]

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("🚲 זמינים", int(latest["bikes_available"].sum()))
    c2.metric("⚡ חשמליים", int(latest["bikes_electric"].sum()))
    c3.metric("🔵 רגילים", int(latest["bikes_regular"].sum()))
    c4.metric("🔴 ריקות", int((latest["bikes_available"]==0).sum()))
    c5.metric("🚫 עם מושבתים", int((latest["bikes_disabled"]>0).sum()))

    st.markdown("---")
    st.subheader("כמות אופניים נוכחית לפי תחנה")
    st.caption("לחץ על בר כדי לראות גרף מפורט")

    df_bar = latest.sort_values("bikes_available", ascending=True).copy()
    df_bar["hover"] = df_bar.apply(
        lambda r: f"רגיל: {int(r.bikes_regular)} | חשמלי: {int(r.bikes_electric)} | מושבת: {int(r.bikes_disabled)} | עגינות: {int(r.docks_free)}", axis=1)

    fig_bar = go.Figure(go.Bar(
        x=df_bar["bikes_available"], y=df_bar["station_name"],
        orientation="h",
        marker_color=df_bar["bikes_available"].apply(avail_color),
        text=df_bar["bikes_available"], textposition="outside",
        hovertemplate="<b>%{y}</b><br>%{hovertext}<extra></extra>",
        hovertext=df_bar["hover"], customdata=df_bar["station_name"],
    ))
    fig_bar.update_layout(
        height=max(500, len(df_bar)*22),
        xaxis_title="אופניים זמינים", yaxis_title="",
        plot_bgcolor="#fafafa",
        xaxis=dict(showgrid=True, gridcolor="#e5e7eb"),
        margin=dict(l=10,r=60,t=20,b=20),
    )
    sel2 = st.plotly_chart(fig_bar, use_container_width=True, on_select="rerun", key="overview_bar")
    if sel2 and sel2.get("selection",{}).get("points"):
        pt = sel2["selection"]["points"][0]
        clicked = pt.get("y") or (pt.get("customdata") if isinstance(pt.get("customdata"), str) else None)
        if clicked:
            navigate_to(clicked)

    st.markdown("---")
    st.subheader(f"התפלגות תחנות לפי ממוצע אופניים — {tf_label}")
    st.caption("hover על הבר לראות אילו תחנות בכל קבוצה")

    if n_rows > 0:
        histo_df = db.get_avg_bikes_histogram(tf_hours)
        if not histo_df.empty:
            histo_df["bucket"] = histo_df["avg_bikes"].apply(assign_bucket)
            bucket_data = []
            for label in BUCKET_LABELS:
                group = histo_df[histo_df["bucket"]==label]
                bucket_data.append({"bucket":label,"count":len(group),
                                    "stations":"<br>".join(group["station_name"].tolist()) or "—"})
            bdf = pd.DataFrame(bucket_data)
            color_map = dict(zip(BUCKET_LABELS, BUCKET_COLORS))
            fig_hist = go.Figure(go.Bar(
                x=bdf["bucket"], y=bdf["count"],
                marker_color=[color_map[b] for b in bdf["bucket"]],
                text=bdf["count"], textposition="outside",
                hovertemplate="<b>%{x} אופניים</b><br>%{y} תחנות:<br>%{customdata}<extra></extra>",
                customdata=bdf["stations"],
            ))
            fig_hist.update_layout(
                xaxis_title="ממוצע אופניים זמינים", yaxis_title="מספר תחנות",
                plot_bgcolor="#fafafa",
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="#e5e7eb"),
                height=370, margin=dict(l=10,r=10,t=20,b=20),
            )
            st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("צריך נתונים היסטוריים להיסטוגרמה זו.")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════
  elif page == "🏆 לידרבורד מחסור":
    st.title(f"🏆 לידרבורד מחסור — {tf_label}")

    if n_rows == 0:
        st.warning("אין נתונים היסטוריים עדיין.")
        st.stop()

    df = db.get_shortage_leaderboard(tf_hours)
    if df.empty:
        st.info("אין נתונים לטווח הזמן שנבחר.")
        st.stop()

    st.subheader("תחנות הריקות הכי הרבה זמן")
    st.caption("לחץ על בר כדי לראות גרף מפורט")

    top = df.head(20).sort_values("pct_empty", ascending=True)
    top["hover_text"] = top.apply(
        lambda r: f"ממוצע אופניים: {r.avg_bikes}<br>מקסימום: {r.max_bikes}<br>שעות ריק (הערכה): {r.max_empty_streak_h}", axis=1)

    fig_lb = go.Figure(go.Bar(
        x=top["pct_empty"], y=top["station_name"], orientation="h",
        marker=dict(color=top["pct_empty"],
                    colorscale=[[0,"#22c55e"],[0.5,"#f59e0b"],[1,"#ef4444"]],
                    cmin=0, cmax=100),
        text=[f"{v}%" for v in top["pct_empty"]], textposition="outside",
        hovertemplate="<b>%{y}</b><br>% זמן ריק: %{x}%<br>%{hovertext}<extra></extra>",
        hovertext=top["hover_text"], customdata=top["station_name"],
    ))
    fig_lb.update_layout(
        height=max(450, len(top)*26),
        xaxis=dict(title="% זמן ריק", range=[0,115], showgrid=True, gridcolor="#e5e7eb"),
        yaxis_title="", plot_bgcolor="#fafafa",
        margin=dict(l=10,r=70,t=20,b=20),
    )
    sel3 = st.plotly_chart(fig_lb, use_container_width=True, on_select="rerun", key="leaderboard_bar")
    if sel3 and sel3.get("selection",{}).get("points"):
        pt = sel3["selection"]["points"][0]
        clicked = pt.get("y") or (pt.get("customdata") if isinstance(pt.get("customdata"), str) else None)
        if clicked:
            navigate_to(clicked)

    st.markdown("---")
    st.subheader("תחנות עם הכי הרבה אופניים מושבתים")

    df_dis = db.get_disabled_leaderboard(tf_hours)
    if not df_dis.empty:
        top_dis = df_dis.head(15).sort_values("avg_disabled", ascending=True)
        top_dis["hover_dis"] = top_dis.apply(
            lambda r: f"מקסימום: {r.max_disabled}<br>ממוצע זמינים: {r.avg_available}", axis=1)
        fig_dis = go.Figure(go.Bar(
            x=top_dis["avg_disabled"], y=top_dis["station_name"], orientation="h",
            marker_color="#6b7280",
            text=[f"{v}" for v in top_dis["avg_disabled"]], textposition="outside",
            hovertemplate="<b>%{y}</b><br>ממוצע מושבתים: %{x}<br>%{hovertext}<extra></extra>",
            hovertext=top_dis["hover_dis"], customdata=top_dis["station_name"],
        ))
        fig_dis.update_layout(
            height=max(380, len(top_dis)*26),
            xaxis=dict(title="ממוצע אופניים מושבתים", showgrid=True, gridcolor="#e5e7eb"),
            yaxis_title="", plot_bgcolor="#fafafa",
            margin=dict(l=10,r=60,t=20,b=20),
        )
        sel_dis = st.plotly_chart(fig_dis, use_container_width=True, on_select="rerun", key="disabled_bar")
        if sel_dis and sel_dis.get("selection",{}).get("points"):
            pt = sel_dis["selection"]["points"][0]
            clicked = pt.get("y") or (pt.get("customdata") if isinstance(pt.get("customdata"), str) else None)
            if clicked:
                navigate_to(clicked)
    else:
        st.info("אין נתוני אופניים מושבתים לטווח הזמן שנבחר.")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — STATION DEEP DIVE
# ══════════════════════════════════════════════════════════════════════════════
  elif page == "🔍 תחנה ספציפית":
    st.title("🔍 ניתוח תחנה ספציפית")

    if n_rows == 0:
        st.warning("אין נתונים היסטוריים עדיין.")
        st.stop()

    all_stations = db.get_all_station_names()
    default_idx  = 0
    if st.session_state.get("selected_station") in all_stations:
        default_idx = all_stations.index(st.session_state["selected_station"])

    selected = st.selectbox("בחר תחנה", all_stations, index=default_idx)
    st.session_state["selected_station"] = selected

    ts_df = db.get_station_timeseries(selected, tf_hours)
    if ts_df.empty:
        st.info("אין נתונים לתחנה זו בטווח הזמן שנבחר.")
        st.stop()

    last = ts_df.iloc[-1]
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("⚡ חשמלי",   int(last["bikes_electric"]))
    c2.metric("🚲 רגיל",    int(last["bikes_regular"]))
    c3.metric("🚫 מושבתים", int(last["bikes_disabled"]))
    c4.metric("🔒 עגינות",  int(last["docks_free"]))

    st.markdown("---")
    st.subheader("אופניים זמינים לאורך זמן")
    fig_ts = go.Figure()
    fig_ts.add_trace(go.Scatter(
        x=ts_df["ts"], y=ts_df["bikes_electric"],
        name="חשמלי", fill="tozeroy",
        line=dict(color="#f59e0b", width=2),
        hovertemplate="חשמלי: %{y}<extra></extra>",
        stackgroup="one",
    ))
    fig_ts.add_trace(go.Scatter(
        x=ts_df["ts"], y=ts_df["bikes_regular"],
        name="רגיל", fill="tonexty",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="רגיל: %{y}<extra></extra>",
        stackgroup="one",
    ))
    fig_ts.update_layout(
        hovermode="x unified",
        xaxis_title="זמן", yaxis_title="אופניים",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="#fafafa", height=340,
        margin=dict(l=10,r=10,t=30,b=20),
    )
    st.plotly_chart(fig_ts, use_container_width=True)

    st.markdown("---")
    st.subheader("מפת חום — ממוצע לפי שעה ויום")
    days_back = max(1, tf_hours//24)
    hm_df = db.get_hourly_heatmap(selected, days_back)
    if not hm_df.empty:
        pivot = hm_df.pivot(index="dow", columns="hour", values="avg_bikes").reindex(range(7))
        pivot.index = [DOW_LABELS[i] for i in range(7)]
        fig_hm = px.imshow(
            pivot,
            color_continuous_scale=["#ef4444","#f59e0b","#22c55e"],
            labels={"x":"שעה","y":"יום","color":"ממוצע אופניים"},
            aspect="auto", text_auto=".1f",
        )
        fig_hm.update_xaxes(
            tickvals=list(range(0,24,2)),
            ticktext=[f"{h:02d}:00" for h in range(0,24,2)],
        )
        fig_hm.update_layout(height=300, margin=dict(l=10,r=10,t=20,b=20))
        st.plotly_chart(fig_hm, use_container_width=True)
    else:
        st.info("צריך יותר נתונים לתצוגת מפת חום.")

    st.markdown("---")
    st.subheader(f"אירועי הוספת אופניים (קפיצה של {VAN_JUMP_THRESHOLD}+)")
    van_df = db.get_van_events(selected, tf_hours, VAN_JUMP_THRESHOLD)
    if van_df.empty:
        st.info("לא זוהו אירועי הוספה בטווח הזמן שנבחר.")
    else:
        fig_van = go.Figure(go.Scatter(
            x=van_df["ts"], y=van_df["delta"],
            mode="markers+text",
            marker=dict(size=van_df["delta"]*4, color="#8b5cf6",
                        symbol="triangle-up", opacity=0.85),
            text=[f"+{int(d)}" for d in van_df["delta"]],
            textposition="top center",
            hovertemplate="<b>%{x}</b><br>תוספת: +%{y} אופניים<extra></extra>",
        ))
        fig_van.update_layout(
            xaxis_title="זמן", yaxis_title="אופניים שנוספו",
            plot_bgcolor="#fafafa", height=260,
            margin=dict(l=10,r=10,t=20,b=20),
        )
        st.plotly_chart(fig_van, use_container_width=True)
        st.caption(f"סה\"כ {len(van_df)} אירועי הוספה")
