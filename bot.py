import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define a simple HTTP handler for health checks
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

# Function to run the health check server
def run_health_server():
    # Bind to port 8000 (Azure App Service pings this port)
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    logger.info("Starting health check server on port 8000")
    server.serve_forever()

# Start the health check server in a background thread
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Set up Discord bot intents
intents = discord.Intents.default()
intents.message_content = True

# Create the bot instance with a command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)

# on_ready event: sync commands and log startup
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        logger.error("Error syncing commands: %s", e)
    logger.info("Logged in as %s", bot.user)

# on_message event: reply to "ping" messages
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.content.lower() == "ping":
        logger.info("Ping received from %s", message.author)
        await message.channel.send("pong!")
    await bot.process_commands(message)

# Regular command example: replies with "Hello!" when a user types "!hello"
@bot.command()
async def hello(ctx):
    logger.info("Received hello command from %s", ctx.author)
    await ctx.send("Hello!")

# Run the bot using the token from environment variables
token = os.getenv('TOKEN')
if not token:
    logger.error("TOKEN not found in environment variables.")
else:
    logger.info("Starting Discord bot...")
    bot.run(token)
