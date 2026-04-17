#!/bin/bash
set -e

echo "========================================="
echo " Starting production deployment on Server"
echo "========================================="

# CORRECT PATHS - backend runs from /root/cybrain-backend
export BACKEND_DIR="/root/cybrain-backend"
export NGINX_WEBROOT="/var/www/cybrain"

echo "1. Unpacking deploy package..."
rm -rf /tmp/deploy_cybrain
mkdir -p /tmp/deploy_cybrain
python3 -m zipfile -e /tmp/deploy_package.zip /tmp/deploy_cybrain

echo "2. Deploying Backend (to /root/cybrain-backend)..."
# Sync app files only - preserve .env and venv
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'venv' \
  "/tmp/deploy_cybrain/backend/" "$BACKEND_DIR/"
chown -R root:root "$BACKEND_DIR"

echo "3. Deploying Frontend dist to Nginx web root..."
if [ -d "/tmp/deploy_cybrain/dist" ]; then
    # Backup old build
    if [ -d "$NGINX_WEBROOT" ]; then
        mv "$NGINX_WEBROOT" "${NGINX_WEBROOT}_backup_$(date +%s)" 2>/dev/null || true
    fi
    mkdir -p "$NGINX_WEBROOT"
    cp -r /tmp/deploy_cybrain/dist/* "$NGINX_WEBROOT/"
    chown -R www-data:www-data "$NGINX_WEBROOT"
    chmod -R 755 "$NGINX_WEBROOT"
    echo "   Frontend deployed to $NGINX_WEBROOT"
else
    echo "   Warning: dist/ not found. Skipping frontend update."
fi

echo "4. Restarting services..."
systemctl daemon-reload
systemctl restart cybrain-backend.service
sleep 3
systemctl status cybrain-backend.service --no-pager -l | head -20
systemctl reload nginx.service

echo "5. Smoke test backend..."
sleep 2
curl -s http://127.0.0.1:8000/api/health || echo "WARNING: /api/health not responding"
curl -s http://127.0.0.1:8000/api/stats || echo "WARNING: /api/stats not responding"

echo "6. Cleanup..."
rm -rf /tmp/deploy_cybrain /tmp/deploy_package.zip /tmp/deploy_script.sh

echo "========================================="
echo " Deployment Successfully Finished!"
echo "========================================="
