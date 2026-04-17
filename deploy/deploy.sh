#!/bin/bash
# =============================================================
# Quantum Trading V3 — VPS Deployment Script
# =============================================================
# Usage: ./deploy.sh [setup|start|stop|status|logs]
# 
# Requirements:
#   - Ubuntu 22.04+ / Debian 12+
#   - Python 3.11+
#   - Node.js 18+
#   - MongoDB 7+
#   - 2 vCPU, 4GB RAM minimum
# =============================================================

set -e

APP_DIR="/opt/quantum-trading"
BACKEND_DIR="$APP_DIR/backend"
FRONTEND_DIR="$APP_DIR/frontend"
VENV_DIR="$APP_DIR/.venv"

case "${1:-help}" in

setup)
    echo "=== Setting up Quantum Trading V3 ==="
    
    # System deps
    sudo apt-get update
    sudo apt-get install -y python3.11 python3.11-venv python3-pip nodejs npm nginx supervisor
    
    # MongoDB
    if ! command -v mongod &> /dev/null; then
        echo "Installing MongoDB..."
        curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
        echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
        sudo apt-get update
        sudo apt-get install -y mongodb-org
        sudo systemctl start mongod
        sudo systemctl enable mongod
    fi

    # Python venv
    python3.11 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    pip install -r "$BACKEND_DIR/requirements.txt"
    
    # Frontend build
    cd "$FRONTEND_DIR"
    npm install --legacy-peer-deps
    npm run build
    
    # Create .env if not exists
    if [ ! -f "$BACKEND_DIR/.env" ]; then
        cp "$APP_DIR/deploy/.env.template" "$BACKEND_DIR/.env"
        echo ">>> Edit $BACKEND_DIR/.env with your credentials"
    fi
    
    # Supervisor config
    sudo cp "$APP_DIR/deploy/supervisor.conf" /etc/supervisor/conf.d/quantum-trading.conf
    sudo supervisorctl reread
    sudo supervisorctl update
    
    # Nginx config
    sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/quantum-trading
    sudo ln -sf /etc/nginx/sites-available/quantum-trading /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    
    echo "=== Setup complete ==="
    echo "1. Edit $BACKEND_DIR/.env with your API keys"
    echo "2. Run: ./deploy.sh start"
    ;;

start)
    echo "Starting services..."
    sudo supervisorctl start quantum-backend
    echo "Backend started"
    ;;

stop)
    echo "Stopping services..."
    sudo supervisorctl stop quantum-backend
    echo "Services stopped"
    ;;

status)
    sudo supervisorctl status quantum-backend
    echo ""
    echo "=== Live Data Status ==="
    curl -s http://localhost:8001/api/v3/live-status 2>/dev/null | python3 -m json.tool || echo "Backend not responding"
    ;;

logs)
    echo "=== Backend Logs ==="
    tail -f /var/log/supervisor/quantum-backend.err.log
    ;;

*)
    echo "Usage: $0 {setup|start|stop|status|logs}"
    exit 1
    ;;
esac
