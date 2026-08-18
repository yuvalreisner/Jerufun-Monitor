#!/bin/bash
REPO=/home/opc/Jerufun-Monitor
DB=$REPO/jerufun.db
STATIONS=$(sqlite3 $DB 'SELECT COUNT(DISTINCT station_id) FROM snapshots WHERE ts=(SELECT MAX(ts) FROM snapshots);')
BIKES=$(sqlite3 $DB 'SELECT SUM(bikes_regular+bikes_electric) FROM snapshots WHERE ts=(SELECT MAX(ts) FROM snapshots);')
MSG="auto update $(TZ=Asia/Jerusalem date '+%Y-%m-%d %H:%M') | ${STATIONS} stations | ${BIKES} bikes"

# Save freshly built dashboard
TMPFILE=$(mktemp)
cp $REPO/dashboard.html $TMPFILE

# Push dashboard.html to main (production always up-to-date)
CURRENT=$(git -C $REPO branch --show-current)
git -C $REPO stash --include-untracked -q 2>/dev/null || true
git -C $REPO checkout main -q
cp $TMPFILE $REPO/dashboard.html
git -C $REPO add dashboard.html
git -C $REPO commit -m "$MSG" || echo "nothing to commit"
git -C $REPO push origin main
git -C $REPO checkout "$CURRENT" -q
git -C $REPO stash pop -q 2>/dev/null || true
rm -f $TMPFILE
