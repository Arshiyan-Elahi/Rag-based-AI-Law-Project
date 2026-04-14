#!/bin/bash
# deploy.sh — Production deployment script for Cybrain QS
# Run ON the server: bash /tmp/deploy.sh

set -e

echo "======================================"
echo " Cybrain QS — Production Deployment"
echo "======================================"

# ── 1. Locate the active project ──────────────────────────────────────────
PROJECT_DIR=""
for candidate in /root/AI-Law-Firm /root/ai-law-firm /root/cybrain /var/www/ai-law /var/www/cybrain /root/app /root/project; do
  if [ -f "$candidate/package.json" ] || [ -d "$candidate/frontend" ] || [ -d "$candidate/Main" ]; then
    PROJECT_DIR="$candidate"
    break
  fi
done

if [ -z "$PROJECT_DIR" ]; then
  echo "[FIND] Searching for project..."
  PROJECT_DIR=$(find /root /var/www /home /srv /opt -maxdepth 5 -name "vite.config.js" 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
fi

if [ -z "$PROJECT_DIR" ]; then
  echo "[ERROR] Cannot locate project. Scanning deeper..."
  find / -maxdepth 6 -name "vite.config.js" 2>/dev/null | head -5
  exit 1
fi

echo "[OK] Project found at: $PROJECT_DIR"

# ── 2. Identify nginx web root ────────────────────────────────────────────
NGINX_ROOT=$(grep -r "root " /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | grep -v "#" | awk '{print $3}' | tr -d ';' | head -1)
echo "[OK] Nginx root: ${NGINX_ROOT:-'not found yet'}"

# ── 3. Find backend systemd service ───────────────────────────────────────
BACKEND_SERVICE=$(systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -iE "uvicorn|gunicorn|cybrain|fastapi|backend|app" | awk '{print $1}' | head -1)
echo "[OK] Backend service: ${BACKEND_SERVICE:-'not found'}"

# ── 4. Show current state ─────────────────────────────────────────────────
echo ""
echo "=== CURRENT SERVER STATE ==="
echo "Project dir: $PROJECT_DIR"
ls "$PROJECT_DIR" 2>/dev/null || true
echo ""
echo "=== NGINX CONFIG ==="
cat /etc/nginx/sites-enabled/* 2>/dev/null || cat /etc/nginx/conf.d/*.conf 2>/dev/null || echo "No nginx config found"
echo ""
echo "=== RUNNING SERVICES ==="
systemctl list-units --type=service --state=running --no-pager 2>/dev/null | grep -v "^$" || true
echo ""
echo "=== GIT STATUS ==="
cd "$PROJECT_DIR" && git log --oneline -5 2>/dev/null || echo "Not a git repo or no commits"
echo "=== BRANCH ==="
git branch 2>/dev/null || true
echo ""
echo "[AUDIT COMPLETE] Review above before proceeding."
