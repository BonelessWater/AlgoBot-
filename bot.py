import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Configure logging to print info level messages
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set up intents (enable message content for reading messages)
intents = discord.Intents.default()
intents.message_content = True

# Create the bot instance with the specified command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)

# on_ready event: this will be called once when the bot logs in.
@bot.event
async def on_ready():
    try:
        # Sync slash commands (this ensures that your slash commands are registered)
        await bot.tree.sync()
    except Exception as e:
        logger.error("Error syncing commands: %s", e)
    logger.info('Logged in as %s', bot.user)

# on_message event: listen for messages and reply accordingly.
@bot.event
async def on_message(message):
    # Ignore messages sent by the bot itself
    if message.author == bot.user:
        return

    # Example: reply to a "ping" message with "pong!"
    if message.content.lower() == "ping":
        logger.info("Ping received from %s", message.author)
        await message.channel.send("pong!")

    # Process commands if the message starts with the command prefix.
    await bot.process_commands(message)

# Regular command: replies with "Hello!" when a user types "!hello"
@bot.command()
async def hello(ctx):
    logger.info("Received hello command from %s", ctx.author)
    await ctx.send('Hello!')

# Slash command: greet a specified user when the command /greet is used
@bot.tree.command(name="greet", description="Greet a user")
async def greet(interaction: discord.Interaction, user: discord.Member):
    logger.info("Greet command used by %s for %s", interaction.user, user)
    await interaction.response.send_message(f"Hello, {user.mention}!")

# Regular command: sends an embed message when a user types "!embed"
@bot.command()
async def embed(ctx):
    logger.info("Embed command invoked by %s", ctx.author)
    embed = discord.Embed(
        title="Example Embed",
        description="This is an example embed.",
        color=0x00ff00
    )
    embed.add_field(name="Field 1", value="Value 1", inline=False)
    embed.add_field(name="Field 2", value="Value 2", inline=False)
    await ctx.send(embed=embed)

# Run the bot using the token stored in an environment variable
token = os.getenv('TOKEN')
if token is None:
    logger.error("TOKEN not found in environment variables.")
else:
    logger.info("Starting bot...")
    bot.run(token)
    
