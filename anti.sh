#!/bin/bash

# --- Discord VPS Creator Bot Uninstaller ---

CONTAINER_NAME="vps-creator-bot"

echo "This script will stop and remove the bot's Docker container."
read -p "Are you sure you want to proceed? (y/N): " choice

if [[ "$choice" =~ ^[Yy]$ ]]; then
    if docker ps -a --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
        echo "Stopping and removing the container '$CONTAINER_NAME'..."
        docker stop "$CONTAINER_NAME"
        docker rm "$CONTAINER_NAME"
        echo "The bot container has been removed."
    else
        echo "No container named '$CONTAINER_NAME' was found. Nothing to do."
    fi
else
    echo "Operation cancelled."
fi
