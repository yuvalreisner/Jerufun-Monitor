#!/bin/bash
DB=/home/opc/Jerufun-Monitor/jerufun.db
STATIONS=$(sqlite3 $DB 'SELECT COUNT(DISTINCT station_id) FROM snapshots WHERE ts=(SELECT MAX(ts) FROM snapshots);')
BIKES=$(sqlite3 $DB 'SELECT SUM(bikes_regular+bikes_electric) FROM snapshots WHERE ts=(SELECT MAX(ts) FROM snapshots);')
MSG="auto update $(date '+%Y-%m-%d %H:%M') | ${STATIONS} stations | ${BIKES} bikes"
git -C /home/opc/Jerufun-Monitor commit -m "$MSG"
git -C /home/opc/Jerufun-Monitor push
