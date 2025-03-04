import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Set up intents (enable message content for reading messages)
intents = discord.Intents.default()
intents.message_content = True

# Create the bot instance with the specified command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)


# on_ready event: this will be called once when the bot logs in.
@bot.event
async def on_ready():
    # Sync slash commands (this ensures that your slash commands are registered)
    await bot.tree.sync()
    print(f'We have logged in as {bot.user}')


# Regular command: replies with "Hello!" when a user types "!hello"
@bot.command()
async def hello(ctx):
    await ctx.send('Hello!')


# Slash command: greet a specified user when the command /greet is used
@bot.tree.command(name="greet", description="Greet a user")
async def greet(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(f"Hello, {user.mention}!")


# Regular command: sends an embed message when a user types "!embed"
@bot.command()
async def embed(ctx):
    embed = bot.Embed(title="Example Embed",
                          description="This is an example embed.",
                          color=0x00ff00)
    embed.add_field(name="Field 1", value="Value 1", inline=False)
    embed.add_field(name="Field 2", value="Value 2", inline=False)
    await ctx.send(embed=embed)

# Run the bot using the token stored in an environment variable
bot.run(os.getenv('TOKEN'))

