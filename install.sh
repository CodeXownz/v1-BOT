#!/bin/bash

# --- Discord VPS Creator Bot Installer ---

echo "Starting the setup for your Discord VPS Creator Bot..."

# Prompt for bot token and other credentials
read -p "Enter your Discord Bot Token: " BOT_TOKEN
read -p "Enter your Admin User IDs (comma-separated, e.g., 12345,67890): " ADMIN_IDS
read -p "Enter the Public IP of your VPS: " PUBLIC_IP
read -p "Enter your Bot's Client ID (for slash command sync): " YOUR_BOT_ID

# Set the name for the Docker image and container
IMAGE_NAME="discord-vps-bot"
CONTAINER_NAME="vps-creator-bot"

# Check if the container is already running
if docker ps -a --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    echo "A container with the name '$CONTAINER_NAME' already exists."
    echo "Stopping and removing the existing container..."
    docker stop "$CONTAINER_NAME"
    docker rm "$CONTAINER_NAME"
fi

# Build the Docker image
echo "Building the Docker image '$IMAGE_NAME'..."
if ! docker build -t "$IMAGE_NAME" .; then
    echo "Error: Failed to build the Docker image. Please check the Dockerfile."
    exit 1
fi

# Run the Docker container with the provided environment variables
echo "Running the bot in a new container..."
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e BOT_TOKEN="$BOT_TOKEN" \
    -e ADMIN_IDS="$ADMIN_IDS" \
    -e PUBLIC_IP="$PUBLIC_IP" \
    -e YOUR_BOT_ID="$YOUR_BOT_ID" \
    "$IMAGE_NAME"

echo "Setup complete! Your bot should now be running in a Docker container."
echo "Check your Discord server to confirm it is online."
