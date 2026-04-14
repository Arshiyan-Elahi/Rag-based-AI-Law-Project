#!/bin/bash
set -e

echo "========================================="
echo " Starting production deployment on Server"
echo "========================================="

export FRONTEND_DIR="/opt/hybrid-rag-isolated/frontend"
export BACKEND_DIR="/opt/hybrid-rag-isolated/backend"
export NGINX_WEBROOT="/var/www/cybrain"

echo "1. Unpacking deploy package..."
rm -rf /tmp/deploy_cybrain
mkdir -p /tmp/deploy_cybrain
# Note: using python to ensure zip extracts without installing external packages
python3 -m zipfile -e /tmp/deploy_package.zip /tmp/deploy_cybrain

echo "2. Deploying Backend..."
# Safely copy backend python files without overriding venv or .env
rsync -avz --exclude '__pycache__' "/tmp/deploy_cybrain/backend/" "$BACKEND_DIR/"
# Fix permissions
chown -R root:root "$BACKEND_DIR"

echo "3. Deploying Frontend source code..."
rsync -avz --exclude 'node_modules' --exclude 'dist' "/tmp/deploy_cybrain/src/" "$FRONTEND_DIR/src/"
cp /tmp/deploy_cybrain/package*.json "$FRONTEND_DIR/"
cp /tmp/deploy_cybrain/vite.config.js "$FRONTEND_DIR/"
cp /tmp/deploy_cybrain/index.html "$FRONTEND_DIR/"

if [ -d "/tmp/deploy_cybrain/dist" ]; then
    echo "4. Deploying Pre-built dist to Nginx Web Root..."
    # Keep backup of previous static build just in case
    mv "$NGINX_WEBROOT" "${NGINX_WEBROOT}_backup_$(date +%s)" 2>/dev/null || true
    mkdir -p "$NGINX_WEBROOT"
    cp -r /tmp/deploy_cybrain/dist/* "$NGINX_WEBROOT/"
    chown -R www-data:www-data "$NGINX_WEBROOT"
    chmod -R 755 "$NGINX_WEBROOT"
else
    echo "4. Warning: dist/ directory not found in zip. Skipping static update."
fi

echo "5. Restarting services..."
systemctl daemon-reload
systemctl restart cybrain-backend.service
systemctl reload nginx.service

echo "6. Cleanup..."
rm -rf /tmp/deploy_cybrain /tmp/deploy_package.zip /tmp/deploy_script.sh

echo "========================================="
echo " Deployment Successfully Finished!"
echo "========================================="
