#!/bin/bash
set -e

echo "Starting deployment setup for Dobrinya VPN Bot..."

# Update system and install necessary packages
echo "Installing python3-venv and python3-pip..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv and install dependencies
echo "Installing Python dependencies..."
source venv/bin/activate
pip install -r requirements.txt

# Determine current directory and user
APP_DIR=$(pwd)
CURRENT_USER=$(whoami)

echo "Generating systemd service files..."

# Generate dobrinya-bot.service
sed -e "s|{{APP_DIR}}|$APP_DIR|g" -e "s|{{USER}}|$CURRENT_USER|g" dobrinya-bot.service.template > dobrinya-bot.service

# Generate dobrinya-sub.service
sed -e "s|{{APP_DIR}}|$APP_DIR|g" -e "s|{{USER}}|$CURRENT_USER|g" dobrinya-sub.service.template > dobrinya-sub.service

echo "Copying services to /etc/systemd/system/..."
sudo cp dobrinya-bot.service /etc/systemd/system/
sudo cp dobrinya-sub.service /etc/systemd/system/

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling and starting services..."
sudo systemctl enable --now dobrinya-bot dobrinya-sub

echo "Deployment complete!"
echo "Please make sure to configure your .env file."
