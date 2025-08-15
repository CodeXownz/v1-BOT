# Discord VPS Creator Bot

This is a private, admin-only Discord bot that allows you to manage VPS instances directly from a Discord server using Docker. It is designed for personal or small-team use where access is restricted to a few trusted users.

The bot uses SQLite for a lightweight, reliable database to keep track of your VPS instances.

## Prerequisites

* **Docker:** The bot relies on Docker to create and manage containers. Ensure Docker is installed and configured on your host machine.
* **Discord Bot Token:** You need a bot token from the Discord Developer Portal.
* **Python 3:** The bot is written in Python.
* **Shell Environment:** The provided `.sh` scripts are for Linux/macOS environments.

## Setup

1.  **Create a Bot:** Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application. Under the "Bot" tab, get your token.
2.  **Enable Intents:** In the "Bot" tab, enable the **"Message Content Intent"** to allow the bot to read messages.
3.  **Invite the Bot:** Invite the bot to your private server with the necessary permissions (e.g., Administrator).
4.  **Configure Environment Variables:** Before running the `install.sh` script, you must provide your configuration details. The script will prompt you for this information.

## How to Run

1.  **Clone the Repository:**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-folder>
    ```

2.  **Run the Installer:**
    The `install.sh` script will guide you through the setup process, which includes building the Docker image and running the container.
    ```bash
    ./install.sh
    ```

## Usage

* **`/nodedmin`**: Lists all running VPS instances and their details (admin only).
* **`/node`**: Shows the host system's resource usage (CPU, RAM, storage) and the status of all instances.
* **`/regen`**: Regenerates the SSH command for your VPS instance.

## Uninstall

To stop and remove the bot and its container, use the `anti.sh` script.

```bash
./anti.sh
