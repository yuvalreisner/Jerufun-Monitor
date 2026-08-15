#!/bin/bash
# setup.sh — provisions a fresh Oracle Linux VM for a Jerufun Monitor instance
# Run as: bash setup.sh
set -e

echo "=== JeruFun Monitor — VM Setup ==="
echo ""

# ── 1. Collect credentials ───────────────────────────────────────────────────
read -p "GitHub username: " GH_USER
read -s -p "GitHub token (will not be shown): " GH_TOKEN
echo ""
read -p "Google Maps API key (leave empty to skip): " MAPS_KEY
echo ""

REPO_URL="https://${GH_USER}:${GH_TOKEN}@github.com/${GH_USER}/Jerufun-Monitor.git"
INSTALL_DIR="/home/opc/Jerufun-Monitor"

# ── 2. Install Python packages ───────────────────────────────────────────────
echo "[1/5] Installing Python dependencies..."
pip3.9 install pandas==1.5.3 requests --quiet

# ── 3. Clone repo ────────────────────────────────────────────────────────────
echo "[2/5] Cloning repository..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  Directory already exists — pulling latest instead."
    git -C "$INSTALL_DIR" pull
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# ── 4. Configure git ─────────────────────────────────────────────────────────
echo "[3/5] Configuring git..."
git -C "$INSTALL_DIR" config user.name "$GH_USER"
git -C "$INSTALL_DIR" config user.email "${GH_USER}@gmail.com"
git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"

# ── 5. Create api_keys.py ────────────────────────────────────────────────────
echo "[4/5] Creating api_keys.py..."
cat > "$INSTALL_DIR/api_keys.py" << EOF
GOOGLE_MAPS_API_KEY = "${MAPS_KEY}"
EOF
echo "  api_keys.py created."

# ── 6. Create backups directory ──────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/backups"

# ── 7. Set up crontab ────────────────────────────────────────────────────────
echo "[5/5] Setting up crontab..."
(crontab -l 2>/dev/null | grep -v 'Jerufun-Monitor'; cat << EOF
*/15 * * * * cd $INSTALL_DIR && python3.9 collector.py --once >> /home/opc/collector.log 2>&1 && python3.9 build.py >> /home/opc/collector.log 2>&1 && git add dashboard.html template.html build.py && bash $INSTALL_DIR/commit_and_push.sh >> /home/opc/collector.log 2>&1
0 3 * * * gzip -c $INSTALL_DIR/jerufun.db > $INSTALL_DIR/backups/jerufun_\$(date +%Y-%m-%d).db.gz && find $INSTALL_DIR/backups/ -name '*.db.gz' -mtime +30 -delete >> /home/opc/collector.log 2>&1
EOF
) | crontab -

# ── 8. Smoke test ────────────────────────────────────────────────────────────
echo ""
echo "=== Smoke test ==="
cd "$INSTALL_DIR"
python3.9 collector.py --once && python3.9 build.py

echo ""
echo "✅ Setup complete!"
echo "   Dashboard will auto-update every 15 minutes."
echo "   Public URL: https://${GH_USER}.github.io/Jerufun-Monitor/dashboard.html"
