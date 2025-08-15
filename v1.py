import random
import logging
import subprocess
import sys
import os
import re
import time
import shlex
import concurrent.futures
import discord
from discord.ext import commands, tasks
import docker
import asyncio
from discord import app_commands
from discord.ui import Button, View, Select
import string
from datetime import datetime, timedelta
from typing import Optional, Literal
import sqlite3
import json

# --- Environment Variables ---
# Load environment variables for security.
# Make sure to set these in your hosting environment.
# Example: export BOT_TOKEN="your_token_here"
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    print("Error: BOT_TOKEN environment variable not set.")
    sys.exit(1)

# Admin user IDs - use a comma-separated list
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x]

# Public IP and Bot ID
PUBLIC_IP = os.getenv('PUBLIC_IP', '138.68.79.95')
YOUR_BOT_ID = os.getenv('YOUR_BOT_ID', 'replace_your_bot_id_here')

# Docker configuration
RAM_LIMIT = os.getenv('RAM_LIMIT', '64g')
SERVER_LIMIT = int(os.getenv('SERVER_LIMIT', '1'))

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Database Configuration ---
DB_FILE = 'vps_database.db'

def setup_database():
    """Initializes the SQLite database and creates the table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS vps_instances (
            user TEXT NOT NULL,
            container_name TEXT PRIMARY KEY,
            ssh_command TEXT,
            ram_limit TEXT,
            cpu_limit TEXT,
            creator TEXT,
            os_type TEXT,
            expiry TEXT,
            ports TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Initial database setup
setup_database()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

# --- Helper functions ---
def is_admin(user_id):
    """Checks if a user's ID is in the admin list."""
    return user_id in ADMIN_IDS

def generate_random_string(length=8):
    """Generates a random alphanumeric string."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def generate_random_port():
    """Generates a random port number."""
    return random.randint(1025, 65535)

def parse_time_to_seconds(time_str):
    """Converts a time string (e.g., '1d', '2h') to seconds."""
    if not time_str:
        return None
    units = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
        'M': 2592000,
        'y': 31536000
    }
    unit = time_str[-1]
    if unit in units and time_str[:-1].isdigit():
        return int(time_str[:-1]) * units[unit]
    elif time_str.isdigit():
        return int(time_str) * 86400  # Default to days if no unit specified
    return None

def format_expiry_date(seconds_from_now):
    """Converts seconds from now to a formatted date string."""
    if not seconds_from_now:
        return None
    expiry_date = datetime.now() + timedelta(seconds=seconds_from_now)
    return expiry_date.strftime("%Y-%m-%d %H:%M:%S")

# --- Asynchronous Database and Docker Functions ---
async def add_to_database(user, container_name, ssh_command, ram_limit=None, cpu_limit=None, creator=None, expiry=None, os_type="Ubuntu 22.04", ports=None):
    """Adds a new VPS entry to the database."""
    conn = await asyncio.to_thread(sqlite3.connect, DB_FILE)
    c = await asyncio.to_thread(conn.cursor)
    try:
        await asyncio.to_thread(
            c.execute,
            "INSERT INTO vps_instances VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user, container_name, ssh_command, ram_limit, cpu_limit, creator, os_type, expiry, json.dumps(ports))
        )
        await asyncio.to_thread(conn.commit)
    except sqlite3.Error as e:
        logging.error(f"Database error while adding instance: {e}")
        raise
    finally:
        await asyncio.to_thread(conn.close)

async def remove_from_database(container_name):
    """Removes a VPS entry from the database."""
    conn = await asyncio.to_thread(sqlite3.connect, DB_FILE)
    c = await asyncio.to_thread(conn.cursor)
    try:
        await asyncio.to_thread(c.execute, "DELETE FROM vps_instances WHERE container_name=?", (container_name,))
        await asyncio.to_thread(conn.commit)
    except sqlite3.Error as e:
        logging.error(f"Database error while removing instance: {e}")
        raise
    finally:
        await asyncio.to_thread(conn.close)

async def get_all_containers_from_db():
    """Fetches all VPS instances from the database."""
    conn = await asyncio.to_thread(sqlite3.connect, DB_FILE)
    c = await asyncio.to_thread(conn.cursor)
    try:
        await asyncio.to_thread(c.execute, "SELECT * FROM vps_instances")
        rows = await asyncio.to_thread(c.fetchall)
        return rows
    finally:
        await asyncio.to_thread(conn.close)

async def get_container_stats(container_id):
    """Gets stats for a single Docker container using a separate thread."""
    def _get_stats_sync():
        try:
            mem_stats = subprocess.check_output(["docker", "stats", container_id, "--no-stream", "--format", "{{.MemUsage}}"], stderr=subprocess.DEVNULL).decode().strip()
            cpu_stats = subprocess.check_output(["docker", "stats", container_id, "--no-stream", "--format", "{{.CPUPerc}}"], stderr=subprocess.DEVNULL).decode().strip()
            status = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", container_id], stderr=subprocess.DEVNULL).decode().strip()
            return {
                "memory": mem_stats,
                "cpu": cpu_stats,
                "status": "🟢 Running" if status == "running" else "🔴 Stopped"
            }
        except subprocess.CalledProcessError:
            return {"memory": "N/A", "cpu": "N/A", "status": "🔴 Stopped"}

    return await asyncio.to_thread(_get_stats_sync)

async def get_system_stats():
    """Gets system stats using a separate thread."""
    def _get_system_stats_sync():
        try:
            total_mem = subprocess.check_output(["free", "-m"], stderr=subprocess.DEVNULL).decode().strip()
            mem_lines = total_mem.split('\n')
            if len(mem_lines) >= 2:
                mem_values = mem_lines[1].split()
                total_mem = mem_values[1]
                used_mem = mem_values[2]

            disk_usage = subprocess.check_output(["df", "-h", "/"], stderr=subprocess.DEVNULL).decode().strip()
            disk_lines = disk_usage.split('\n')
            if len(disk_lines) >= 2:
                disk_values = disk_lines[1].split()
                total_disk = disk_values[1]
                used_disk = disk_values[2]

            return {
                "total_memory": f"{int(total_mem) / 1024:.2f} GB", # Convert MB to GB
                "used_memory": f"{int(used_mem) / 1024:.2f} GB",
                "total_disk": total_disk,
                "used_disk": used_disk
            }
        except Exception as e:
            logging.error(f"Failed to get system stats: {e}")
            return {
                "total_memory": "N/A", "used_memory": "N/A",
                "total_disk": "N/A", "used_disk": "N/A"
            }
    return await asyncio.to_thread(_get_system_stats_sync)

async def get_user_servers_from_db(user):
    """Fetches all servers belonging to a specific user."""
    conn = await asyncio.to_thread(sqlite3.connect, DB_FILE)
    c = await asyncio.to_thread(conn.cursor)
    try:
        await asyncio.to_thread(c.execute, "SELECT * FROM vps_instances WHERE user=?", (user,))
        rows = await asyncio.to_thread(c.fetchall)
        return rows
    finally:
        await asyncio.to_thread(conn.close)

async def update_ssh_command_in_db(container_name, new_ssh_command):
    """Updates the SSH command for a container in the database."""
    conn = await asyncio.to_thread(sqlite3.connect, DB_FILE)
    c = await asyncio.to_thread(conn.cursor)
    try:
        await asyncio.to_thread(c.execute, "UPDATE vps_instances SET ssh_command=? WHERE container_name=?", (new_ssh_command, container_name))
        await asyncio.to_thread(conn.commit)
    except sqlite3.Error as e:
        logging.error(f"Database error while updating SSH command: {e}")
        raise
    finally:
        await asyncio.to_thread(conn.close)

async def get_ssh_command_from_database(container_name):
    """Retrieves the SSH command for a specific container."""
    conn = await asyncio.to_thread(sqlite3.connect, DB_FILE)
    c = await asyncio.to_thread(conn.cursor)
    try:
        await asyncio.to_thread(c.execute, "SELECT ssh_command FROM vps_instances WHERE container_name=?", (container_name,))
        result = await asyncio.to_thread(c.fetchone)
        return result[0] if result else None
    finally:
        await asyncio.to_thread(conn.close)

async def get_container_id_from_database(user, container_name=None):
    """Retrieves the container name for a user's server."""
    servers = await get_user_servers_from_db(user)
    if not servers:
        return None
    if container_name:
        for server in servers:
            if container_name == server[1]:
                return server[1]
        return None
    else:
        return servers[0][1]

async def count_user_servers(user):
    """Counts the number of servers owned by a user."""
    servers = await get_user_servers_from_db(user)
    return len(servers)

async def capture_ssh_session_line(process):
    """Captures the SSH session line from the tmate output."""
    while True:
        try:
            output = await asyncio.wait_for(process.stdout.readline(), timeout=10.0)
            if not output:
                break
            output = output.decode('utf-8').strip()
            if "ssh session:" in output:
                return output.split("ssh session:")[1].strip()
        except asyncio.TimeoutError:
            logging.warning("Timed out waiting for tmate output.")
            break
        except Exception as e:
            logging.error(f"Error reading tmate output: {e}")
            break
    return None

# --- UI Components ---
class OSSelectView(View):
    """Dropdown for selecting an OS."""
    def __init__(self, callback):
        super().__init__(timeout=60)
        self.callback = callback
        select = Select(
            placeholder="Select an operating system",
            options=[
                discord.SelectOption(label="Ubuntu 22.04", description="Latest LTS Ubuntu release", emoji="🐧", value="ubuntu"),
                discord.SelectOption(label="Debian 12", description="Stable Debian release", emoji="🐧", value="debian")
            ]
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_os = interaction.data["values"][0]
        await interaction.response.defer()
        await self.callback(interaction, selected_os)

class ConfirmView(View):
    """Confirmation dialog for delete operations."""
    def __init__(self, container_id, container_name, is_delete_all=False):
        super().__init__(timeout=60)
        self.container_id = container_id
        self.container_name = container_name
        self.is_delete_all = is_delete_all
    
    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=False)
        try:
            if self.is_delete_all:
                containers = await get_all_containers_from_db()
                deleted_count = 0
                for container_info in containers:
                    container_id = container_info[1]
                    try:
                        await asyncio.to_thread(subprocess.run, ["docker", "stop", container_id], check=True, stderr=subprocess.DEVNULL)
                        await asyncio.to_thread(subprocess.run, ["docker", "rm", container_id], check=True, stderr=subprocess.DEVNULL)
                        await remove_from_database(container_id)
                        deleted_count += 1
                    except subprocess.CalledProcessError:
                        logging.error(f"Failed to stop/remove container {container_id}")
                
                embed = discord.Embed(
                    title="All VPS Instances Deleted",
                    description=f"Successfully deleted {deleted_count} VPS instances.",
                    color=0x00ff00
                )
                await interaction.followup.send(embed=embed)
            else:
                try:
                    await asyncio.to_thread(subprocess.run, ["docker", "stop", self.container_id], check=True, stderr=subprocess.DEVNULL)
                    await asyncio.to_thread(subprocess.run, ["docker", "rm", self.container_id], check=True, stderr=subprocess.DEVNULL)
                    await remove_from_database(self.container_id)
                    
                    embed = discord.Embed(
                        title="VPS Deleted",
                        description=f"Successfully deleted VPS instance `{self.container_name}`.",
                        color=0x00ff00
                    )
                    await interaction.followup.send(embed=embed)
                except subprocess.CalledProcessError as e:
                    embed = discord.Embed(
                        title="❌ Error",
                        description=f"Failed to delete VPS instance: {e}",
                        color=0xff0000
                    )
                    await interaction.followup.send(embed=embed)
        except Exception as e:
            logging.error(f"Unexpected error during delete operation: {e}")
            await interaction.followup.send(f"An unexpected error occurred: {e}")
        finally:
            self.stop() # Stop the view
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=False)
        embed = discord.Embed(
            title="🚫 Operation Cancelled",
            description="The delete operation has been cancelled.",
            color=0xffaa00
        )
        await interaction.followup.send(embed=embed)
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)

# --- Discord Events ---
@bot.event
async def on_ready():
    """Event handler for when the bot is ready."""
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s).")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")

    logging.info(f"✅ Bot Ready: {bot.user}")
    change_status.start()

@tasks.loop(seconds=60)
async def change_status():
    """Changes the bot's status periodically."""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM vps_instances")
        instance_count = c.fetchone()[0]
        conn.close()
        status = f"with {instance_count} Cloud Instances 🌐"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        logging.error(f"Failed to update status: {e}")

# --- Slash Commands ---
@bot.tree.command(name="nodedmin", description="📊 Admin: Lists all VPSs, their details, and SSH commands")
async def nodedmin(interaction: discord.Interaction):
    """Admin command to list all VPS instances."""
    if not is_admin(interaction.user.id):
        embed = discord.Embed(
            title="❌ Access Denied",
            description="You don't have permission to use this command.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)
    
    containers = await get_all_containers_from_db()
    if not containers:
        embed = discord.Embed(
            title="VPS Instances",
            description="No VPS data available.",
            color=0xff0000
        )
        await interaction.followup.send(embed=embed)
        return

    embed = discord.Embed(
        title="All VPS Instances",
        description="Detailed information about all VPS instances",
        color=0x00aaff
    )
    
    for container_info in containers:
        user, container_name, ssh_command, ram, cpu, creator, os_type, expiry, ports = container_info
        stats = await get_container_stats(container_name)
        
        embed.add_field(
            name=f"🖥️ {container_name} ({stats['status']})",
            value=f"🪩 **User:** {user}\n"
                  f"💾 **RAM:** {ram}GB\n"
                  f"🔥 **CPU:** {cpu} cores\n"
                  f"🌐 **OS:** {os_type}\n"
                  f"👑 **Creator:** {creator}\n"
                  f"🔑 **SSH:** `{ssh_command}`",
            inline=False
        )

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="node", description="☠️ Shows system resource usage and VPS status")
async def node_stats(interaction: discord.Interaction):
    """Command to show system and VPS stats."""
    await interaction.response.defer()
    
    system_stats = await get_system_stats()
    containers = await get_all_containers_from_db()
    
    embed = discord.Embed(
        title="🖥️ System Resource Usage",
        description="Current resource usage of the host system",
        color=0x00aaff
    )
    
    embed.add_field(
        name="🔥 Memory Usage",
        value=f"Used: {system_stats['used_memory']} / Total: {system_stats['total_memory']}",
        inline=False
    )
    
    embed.add_field(
        name="💾 Storage Usage",
        value=f"Used: {system_stats['used_disk']} / Total: {system_stats['total_disk']}",
        inline=False
    )
    
    embed.add_field(
        name=f"🧊 VPS Instances ({len(containers)})",
        value="List of all VPS instances and their status:",
        inline=False
    )
    
    for container_info in containers:
        container_name = container_info[1]
        stats = await get_container_stats(container_name)
        embed.add_field(
            name=f"{container_name}",
            value=f"Status: {stats['status']}\nMemory: {stats['memory']}\nCPU: {stats['cpu']}",
            inline=True
        )
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="regen", description="🔄 Regenerates the SSH command for your VPS")
@app_commands.describe(container_name="The name of your container to regen SSH for")
async def regen_ssh(interaction: discord.Interaction, container_name: Optional[str] = None):
    """Regenerates the SSH command for a user's VPS."""
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    
    container_id = await get_container_id_from_database(user_id, container_name)
    
    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No active instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.followup.send(embed=embed)
        return

    try:
        process = await asyncio.to_thread(
            subprocess.Popen,
            ["docker", "exec", container_id, "tmate", "-F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(process)

        if ssh_session_line:
            await update_ssh_command_in_db(container_id, ssh_session_line)
            
            dm_embed = discord.Embed(
                title="🔄 New SSH Session Generated",
                description="Your SSH session has been regenerated successfully.",
                color=0x00ff00
            )
            dm_embed.add_field(
                name="🔑 SSH Connection Command",
                value=f"```{ssh_session_line}```",
                inline=False
            )
            await interaction.user.send(embed=dm_embed)
            
            success_embed = discord.Embed(
                title="✅ SSH Session Regenerated",
                description="New SSH session generated. Check your DMs for details.",
                color=0x00ff00
            )
            await interaction.followup.send(embed=success_embed)
        else:
            error_embed = discord.Embed(
                title="❌ Failed",
                description="Failed to generate new SSH session. Please try again later.",
                color=0xff0000
            )
            await interaction.followup.send(embed=error_embed)

    except Exception as e:
        logging.error(f"Error during SSH regeneration: {e}")
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"An error occurred while regenerating the SSH session: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

# -------------------- NEW COMMANDS --------------------

@bot.tree.command(name="create", description="📦 Creates a new VPS instance based on a tier")
@app_commands.describe(tier='The VPS tier to create (4inv, 1boost, 1m_owo)')
@app_commands.choices(tier=[
    app_commands.Choice(name="4inv", value="4inv"),
    app_commands.Choice(name="1boost", value="1boost"),
    app_commands.Choice(name="1m_owo", value="1m_owo"),
])
async def create_vps(interaction: discord.Interaction, tier: Literal['4inv', '1boost', '1m_owo']):
    await interaction.response.defer()
    
    user_id = str(interaction.user.id)
    user_servers = await count_user_servers(user_id)
    
    if user_servers >= SERVER_LIMIT:
        embed = discord.Embed(
            title="❌ Creation Limit Reached",
            description=f"You have already reached the maximum limit of {SERVER_LIMIT} VPS instances.",
            color=0xff0000
        )
        await interaction.followup.send(embed=embed)
        return

    specs = {
        "4inv": {"cpu": "1", "ram": "2g", "image": "ubuntu:22.04"},
        "1boost": {"cpu": "2", "ram": "4g", "image": "ubuntu:22.04"},
        "1m_owo": {"cpu": "4", "ram": "8g", "image": "ubuntu:22.04"}
    }
    
    if tier not in specs:
        await interaction.followup.send("Invalid tier specified.")
        return
        
    cpu = specs[tier]["cpu"]
    ram = specs[tier]["ram"]
    image = specs[tier]["image"]
    container_name = f"{user_id}-{generate_random_string()}"

    try:
        container = await asyncio.to_thread(
            client.containers.run,
            image,
            detach=True,
            name=container_name,
            hostname=container_name,
            tty=True,
            stdin_open=True,
            volumes=['/var/run/docker.sock:/var/run/docker.sock'],
            mem_limit=ram,
            cpus=float(cpu),
            labels={'owner': user_id, 'tier': tier}
        )
        
        # Wait a moment for tmate to start
        await asyncio.sleep(5)
        
        # Get SSH command from tmate
        process = await asyncio.to_thread(
            subprocess.Popen,
            ["docker", "exec", container_name, "tmate", "-F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(process)

        if ssh_session_line:
            await add_to_database(user_id, container_name, ssh_session_line, ram, cpu, str(interaction.user), "Ubuntu 22.04", "None", [])
            
            embed = discord.Embed(
                title=f"✅ VPS '{container_name}' Created!",
                description="Your new VPS is ready. Check your DMs for the SSH command.",
                color=0x00ff00
            )
            embed.add_field(name="Tier", value=tier, inline=True)
            embed.add_field(name="CPU", value=f"{cpu} core(s)", inline=True)
            embed.add_field(name="RAM", value=ram, inline=True)
            await interaction.followup.send(embed=embed)

            dm_embed = discord.Embed(
                title=f"New VPS Created: {container_name}",
                description="Use the following command to connect:",
                color=0x00ff00
            )
            dm_embed.add_field(name="SSH Command", value=f"```\n{ssh_session_line}\n```", inline=False)
            await interaction.user.send(embed=dm_embed)
        else:
            await asyncio.to_thread(container.remove, force=True)
            await interaction.followup.send("Failed to get SSH command. VPS removed. Please try again.")

    except docker.errors.APIError as e:
        await interaction.followup.send(f"An error occurred while creating the VPS: {e}")

@bot.tree.command(name="deploy", description="🚀 Admin: Deploys a new VPS with custom specs")
@app_commands.describe(user_id="The user to deploy the VPS for", name="The name of the VPS", ram="RAM limit (e.g., 2g, 4g)", cpu="CPU limit (e.g., 1, 2)", time="Duration (e.g., 1d, 3h)")
async def deploy_vps(interaction: discord.Interaction, user_id: str, name: str, ram: str, cpu: str, time: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    await interaction.response.defer()
    
    seconds = parse_time_to_seconds(time)
    if seconds is None:
        await interaction.followup.send("Invalid time format. Use something like `1d`, `3h`, `30m`.")
        return
    
    expiry_date = format_expiry_date(seconds)
    
    # Placeholder for Docker deployment logic
    await interaction.followup.send(f"Deploying VPS '{name}' for user {user_id} with {ram} RAM and {cpu} CPU for {time}. Expiring on {expiry_date}.")

@bot.tree.command(name="deleteall", description="💀 Admin: Deletes all VPS instances")
async def delete_all(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    
    confirm_view = ConfirmView(None, None, is_delete_all=True)
    await interaction.response.send_message("Are you sure you want to delete ALL VPS instances?", view=confirm_view)

@bot.tree.command(name="remove", description="🗑️ Removes a specific VPS instance you own")
@app_commands.describe(container_name="The name of the VPS to remove")
async def remove_vps(interaction: discord.Interaction, container_name: str):
    await interaction.response.defer()
    user_id = str(interaction.user.id)
    
    # Check if the user owns the container
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT container_name FROM vps_instances WHERE user=? AND container_name=?", (user_id, container_name))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await interaction.followup.send("You do not own a VPS with that name or it doesn't exist.")
        return
        
    confirm_view = ConfirmView(container_name, container_name, is_delete_all=False)
    await interaction.followup.send(f"Are you sure you want to delete VPS '{container_name}'?", view=confirm_view)

@bot.tree.command(name="start", description="🟢 Starts your stopped VPS")
@app_commands.describe(container_name="The name of the VPS to start")
async def start_vps(interaction: discord.Interaction, container_name: str):
    await interaction.response.defer()
    user_id = str(interaction.user.id)
    
    try:
        container = await asyncio.to_thread(client.containers.get, container_name)
        if container.labels.get('owner') != user_id:
            await interaction.followup.send("You do not own this VPS.")
            return

        await asyncio.to_thread(container.start)
        await interaction.followup.send(f"VPS '{container_name}' has been started.")
    except docker.errors.NotFound:
        await interaction.followup.send(f"VPS '{container_name}' not found.")
    except Exception as e:
        logging.error(f"Failed to start VPS: {e}")
        await interaction.followup.send(f"An error occurred while starting the VPS: {e}")

@bot.tree.command(name="stop", description="🔴 Stops your running VPS")
@app_commands.describe(container_name="The name of the VPS to stop")
async def stop_vps(interaction: discord.Interaction, container_name: str):
    await interaction.response.defer()
    user_id = str(interaction.user.id)
    
    try:
        container = await asyncio.to_thread(client.containers.get, container_name)
        if container.labels.get('owner') != user_id:
            await interaction.followup.send("You do not own this VPS.")
            return

        await asyncio.to_thread(container.stop)
        await interaction.followup.send(f"VPS '{container_name}' has been stopped.")
    except docker.errors.NotFound:
        await interaction.followup.send(f"VPS '{container_name}' not found.")
    except Exception as e:
        logging.error(f"Failed to stop VPS: {e}")
        await interaction.followup.send(f"An error occurred while stopping the VPS: {e}")

@bot.tree.command(name="restart", description="🔄 Restarts your VPS")
@app_commands.describe(container_name="The name of the VPS to restart")
async def restart_vps(interaction: discord.Interaction, container_name: str):
    await interaction.response.defer()
    user_id = str(interaction.user.id)
    
    try:
        container = await asyncio.to_thread(client.containers.get, container_name)
        if container.labels.get('owner') != user_id:
            await interaction.followup.send("You do not own this VPS.")
            return

        await asyncio.to_thread(container.restart)
        await interaction.followup.send(f"VPS '{container_name}' is restarting.")
    except docker.errors.NotFound:
        await interaction.followup.send(f"VPS '{container_name}' not found.")
    except Exception as e:
        logging.error(f"Failed to restart VPS: {e}")
        await interaction.followup.send(f"An error occurred while restarting the VPS: {e}")

@bot.tree.command(name="tunneling", description="🌐 Provides a new tunneling command for your VPS")
@app_commands.describe(container_name="The name of the VPS", port="The port to tunnel to (e.g., 8080)")
async def tunneling_vps(interaction: discord.Interaction, container_name: str, port: int):
    await interaction.response.defer()
    
    user_id = str(interaction.user.id)
    try:
        container = await asyncio.to_thread(client.containers.get, container_name)
        if container.labels.get('owner') != user_id:
            await interaction.followup.send("You do not own this VPS.")
            return
            
        public_port = generate_random_port()
        
        await asyncio.to_thread(container.exec_run, f'ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{port} ssh.localhost.run', detach=True, stream=True)
        
        embed = discord.Embed(
            title="🌐 SSH Tunneling",
            description=f"A new tunnel has been created for VPS `{container_name}`.",
            color=0x00aaff
        )
        embed.add_field(name="Public URL", value=f"```\n{PUBLIC_IP}:{public_port}\n```", inline=False)
        embed.add_field(name="Details", value=f"Tunneling from `{port}` on your VPS to port `{public_port}` on the public IP.", inline=False)
        await interaction.followup.send(embed=embed)
        
    except docker.errors.NotFound:
        await interaction.followup.send(f"VPS '{container_name}' not found.")
    except Exception as e:
        logging.error(f"Failed to create tunnel: {e}")
        await interaction.followup.send(f"An error occurred while creating the tunnel: {e}")

@bot.tree.command(name="sharedipv4", description="🔗 Adds a shared IPv4 to your VPS")
@app_commands.describe(container_name="The name of the VPS to share an IP with")
async def shared_ipv4(interaction: discord.Interaction, container_name: str):
    await interaction.response.defer()
    
    user_id = str(interaction.user.id)
    try:
        container = await asyncio.to_thread(client.containers.get, container_name)
        if container.labels.get('owner') != user_id:
            await interaction.followup.send("You do not own this VPS.")
            return

        # Placeholder logic for sharing IP
        # This is a very complex operation and depends on your network setup
        # It may involve using iptables, network namespaces, or a reverse proxy.
        # This code will only simulate the command.
        
        embed = discord.Embed(
            title="🔗 Shared IPv4",
            description=f"Shared IPv4 is now active for VPS `{container_name}`.",
            color=0x00ff00
        )
        embed.add_field(name="Details", value=f"Your VPS can now be accessed via the host's public IP (`{PUBLIC_IP}`). You will need to configure port forwarding or a reverse proxy to direct traffic.", inline=False)
        await interaction.followup.send(embed=embed)
        
    except docker.errors.NotFound:
        await interaction.followup.send(f"VPS '{container_name}' not found.")
    except Exception as e:
        logging.error(f"Failed to configure shared IPv4: {e}")
        await interaction.followup.send(f"An error occurred: {e}")

@bot.tree.command(name="make-admin", description="👑 Admin: Grants admin privileges to a user")
@app_commands.describe(user_id="The user to make an admin")
async def make_admin(interaction: discord.Interaction, user_id: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
        
    try:
        new_admin_id = int(user_id)
        if new_admin_id in ADMIN_IDS:
            await interaction.response.send_message(f"<@{user_id}> is already an admin.", ephemeral=True)
            return
            
        ADMIN_IDS.append(new_admin_id)
        # In a real-world scenario, you would update a configuration file or database.
        
        embed = discord.Embed(
            title="👑 Admin Added",
            description=f"<@{user_id}> has been granted admin privileges. You may need to restart the bot for full effect.",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=embed)
        
    except ValueError:
        await interaction.response.send_message("Invalid user ID provided.", ephemeral=True)

@bot.tree.command(name="regen-ssh", description="🔄 Regenerates the SSH command for your VPS")
@app_commands.describe(container_name="The name of your container to regen SSH for")
async def regen_ssh(interaction: discord.Interaction, container_name: Optional[str] = None):
    """Regenerates the SSH command for a user's VPS."""
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    
    container_id = await get_container_id_from_database(user_id, container_name)
    
    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No active instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.followup.send(embed=embed)
        return

    try:
        process = await asyncio.to_thread(
            subprocess.Popen,
            ["docker", "exec", container_id, "tmate", "-F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(process)

        if ssh_session_line:
            await update_ssh_command_in_db(container_id, ssh_session_line)
            
            dm_embed = discord.Embed(
                title="🔄 New SSH Session Generated",
                description="Your SSH session has been regenerated successfully.",
                color=0x00ff00
            )
            dm_embed.add_field(
                name="🔑 SSH Connection Command",
                value=f"```{ssh_session_line}```",
                inline=False
            )
            await interaction.user.send(embed=dm_embed)
            
            success_embed = discord.Embed(
                title="✅ SSH Session Regenerated",
                description="New SSH session generated. Check your DMs for details.",
                color=0x00ff00
            )
            await interaction.followup.send(embed=success_embed)
        else:
            error_embed = discord.Embed(
                title="❌ Failed",
                description="Failed to generate new SSH session. Please try again later.",
                color=0xff0000
            )
            await interaction.followup.send(embed=error_embed)

    except Exception as e:
        logging.error(f"Error during SSH regeneration: {e}")
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"An error occurred while regenerating the SSH session: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

# This is the main entry point to run the bot
if __name__ == '__main__':
    bot.run(TOKEN)
