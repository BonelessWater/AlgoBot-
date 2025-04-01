import os
import logging
import threading
import random
import asyncio
import datetime
import json
import aiohttp
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import youtube_dl
import requests

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
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    logger.info("Starting health check server on port 8000")
    server.serve_forever()

# Start the health check server in a background thread
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Set up Discord bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Enable member intents for welcome messages

# Create the bot instance with a command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Utility Functions ---
def is_admin():
    """Check if the user has administrator permissions"""
    async def predicate(ctx):
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ You need administrator permissions to use this command.")
            return False
        return True
    return commands.check(predicate)

def is_moderator():
    """Check if the user has moderator permissions (kick_members permission)"""
    async def predicate(ctx):
        if not ctx.author.guild_permissions.kick_members:
            await ctx.send("❌ You need moderator permissions to use this command.")
            return False
        return True
    return commands.check(predicate)

def save_to_json(data, filename):
    """Save data to a JSON file"""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

def load_from_json(filename, default=None):
    """Load data from a JSON file with a default value if file doesn't exist"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}

# --- Bot Events ---
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        logger.error("Error syncing commands: %s", e)
    logger.info("Logged in as %s", bot.user)
    
    # Start background tasks
    status_updater.start()
    
    # Load data for utility cog
    utility_cog = bot.get_cog("UtilityCog")
    if utility_cog:
        utility_cog.load_data()
        utility_cog.check_reminders.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have the required permissions to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument: {error.param.name}. Type `!help {ctx.command.name}` for more info.")
    elif isinstance(error, commands.CommandNotFound):
        # Silently ignore command not found errors
        pass
    else:
        logger.error(f"Error in command {ctx.command}: {error}")
        await ctx.send("❌ An error occurred while processing this command.")

@bot.event
async def on_member_join(member):
    # Send welcome message in system channel if it exists
    if member.guild.system_channel:
        embed = discord.Embed(
            title=f"Welcome to {member.guild.name}!",
            description=f"Hello {member.mention}! Thanks for joining us. Use `!help` to see my commands.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        embed.set_footer(text=f"Member #{len(member.guild.members)}")
        await member.guild.system_channel.send(embed=embed)

# Status rotation task
@tasks.loop(minutes=10)
async def status_updater():
    statuses = [
        discord.Game(name="with code"),
        discord.Activity(type=discord.ActivityType.listening, name="commands"),
        discord.Activity(type=discord.ActivityType.watching, name="the server"),
        discord.Game(name="!help for commands")
    ]
    await bot.change_presence(activity=random.choice(statuses))

# --- Music Commands ---
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}
        self.now_playing = {}

    @commands.command(name="join")
    async def join(self, ctx):
        """Join your voice channel"""
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            await channel.connect()
            await ctx.send(f"Joined {channel.name}.")
        else:
            await ctx.send("❌ You're not connected to a voice channel.")

    @commands.command(name="play")
    async def play(self, ctx, *, query: str):
        """Play a song from YouTube"""
        if ctx.voice_client is None:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
                await channel.connect()
            else:
                await ctx.send("❌ You're not connected to a voice channel.")
                return
        
        # Initialize queue for this guild if it doesn't exist
        guild_id = ctx.guild.id
        if guild_id not in self.queue:
            self.queue[guild_id] = []
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'default_search': 'ytsearch',
            'noplaylist': True
        }
        
        try:
            await ctx.send("🔍 Searching...")
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch:{query}" if not query.startswith("http") else query, download=False)
                if 'entries' in info:
                    # It's a search result
                    info = info['entries'][0]
                audio_url = info['url']
                title = info.get('title', 'Unknown Title')
                duration = info.get('duration', 0)
                thumbnail = info.get('thumbnail', '')
                
                # Add to queue
                self.queue[guild_id].append({
                    'url': audio_url,
                    'title': title,
                    'requester': ctx.author.name,
                    'duration': duration,
                    'thumbnail': thumbnail
                })
                
                # Create embed
                embed = discord.Embed(
                    title="Added to Queue",
                    description=f"[{title}]({info.get('webpage_url', '')})",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Duration", value=self._format_duration(duration))
                embed.add_field(name="Requested by", value=ctx.author.name)
                
                if thumbnail:
                    embed.set_thumbnail(url=thumbnail)
                
                await ctx.send(embed=embed)
                
                # If nothing is playing, start the queue
                if not ctx.voice_client.is_playing():
                    await self._play_next(ctx)
        except Exception as e:
            await ctx.send("❌ An error occurred while trying to play the track.")
            logger.error(f"Error in play command: {e}")

    async def _play_next(self, ctx):
        guild_id = ctx.guild.id
        
        if guild_id in self.queue and self.queue[guild_id]:
            # Get the next track
            track = self.queue[guild_id].pop(0)
            self.now_playing[guild_id] = track
            
            # Play the track
            ctx.voice_client.play(
                discord.FFmpegPCMAudio(track['url']),
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    self._play_next(ctx), self.bot.loop
                ) if e is None else logger.error(f"Player error: {e}")
            )
            
            # Send now playing message
            embed = discord.Embed(
                title="Now Playing",
                description=f"{track['title']}",
                color=discord.Color.green()
            )
            embed.add_field(name="Duration", value=self._format_duration(track['duration']))
            embed.add_field(name="Requested by", value=track['requester'])
            
            if track['thumbnail']:
                embed.set_thumbnail(url=track['thumbnail'])
                
            await ctx.send(embed=embed)
        else:
            self.now_playing.pop(guild_id, None)
            await ctx.send("Queue finished. Use `!play` to add more songs.")

    def _format_duration(self, duration):
        if not duration:
            return "Unknown"
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @commands.command(name="queue")
    async def queue(self, ctx):
        """View the current music queue"""
        guild_id = ctx.guild.id
        if guild_id not in self.queue or not self.queue[guild_id]:
            return await ctx.send("The queue is empty.")
        
        embed = discord.Embed(
            title="Music Queue",
            color=discord.Color.blue()
        )
        
        # Show current track
        if guild_id in self.now_playing:
            track = self.now_playing[guild_id]
            embed.add_field(
                name="Now Playing",
                value=f"{track['title']} | Requested by: {track['requester']}",
                inline=False
            )
        
        # Show upcoming tracks
        queue_list = ""
        for i, track in enumerate(self.queue[guild_id][:10], 1):
            queue_list += f"{i}. {track['title']} | {self._format_duration(track['duration'])}\n"
        
        if queue_list:
            embed.add_field(name="Up Next", value=queue_list, inline=False)
            
        if len(self.queue[guild_id]) > 10:
            embed.set_footer(text=f"And {len(self.queue[guild_id]) - 10} more songs...")
            
        await ctx.send(embed=embed)

    @commands.command(name="skip")
    async def skip(self, ctx):
        """Skip the current song"""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("Skipped the current song.")
        else:
            await ctx.send("❌ No audio is playing.")

    @commands.command(name="stop")
    async def stop(self, ctx):
        """Stop playback and clear the queue"""
        guild_id = ctx.guild.id
        if ctx.voice_client and ctx.voice_client.is_playing():
            # Clear the queue
            self.queue[guild_id] = []
            self.now_playing.pop(guild_id, None)
            
            # Stop playback
            ctx.voice_client.stop()
            await ctx.send("Playback stopped and queue cleared.")
        else:
            await ctx.send("❌ No audio is playing.")

    @commands.command(name="leave")
    async def leave(self, ctx):
        """Leave the voice channel"""
        guild_id = ctx.guild.id
        # Clear queue and now playing
        self.queue[guild_id] = []
        self.now_playing.pop(guild_id, None)
        
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("Left the voice channel.")
        else:
            await ctx.send("❌ I'm not connected to a voice channel.")

# --- Moderation Commands ---
class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.warns = load_from_json('warns.json', {})

    def _save_warns(self):
        save_to_json(self.warns, 'warns.json')

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason=None):
        """Kick a member from the server (Requires kick permission)"""
        # Check if bot has permission to kick
        if not ctx.guild.me.guild_permissions.kick_members:
            return await ctx.send("❌ I don't have permission to kick members.")
            
        # Check if the target is higher in role hierarchy
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send("❌ You cannot kick someone with a higher or equal role.")
            
        try:
            await member.kick(reason=reason)
            embed = discord.Embed(
                title="Member Kicked",
                description=f"{member.mention} has been kicked.",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=reason or "No reason provided")
            embed.set_footer(text=f"Kicked by {ctx.author} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
            await ctx.send(embed=embed)
            
            # Log the kick
            logger.info(f"User {member} was kicked by {ctx.author} for reason: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to kick this member.")
        except Exception as e:
            await ctx.send("❌ An error occurred while trying to kick the member.")
            logger.error(f"Error in kick command: {e}")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason=None):
        """Ban a member from the server (Requires ban permission)"""
        # Check if bot has permission to ban
        if not ctx.guild.me.guild_permissions.ban_members:
            return await ctx.send("❌ I don't have permission to ban members.")
            
        # Check if the target is higher in role hierarchy
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send("❌ You cannot ban someone with a higher or equal role.")
            
        try:
            await member.ban(reason=reason)
            embed = discord.Embed(
                title="Member Banned",
                description=f"{member.mention} has been banned.",
                color=discord.Color.dark_red()
            )
            embed.add_field(name="Reason", value=reason or "No reason provided")
            embed.set_footer(text=f"Banned by {ctx.author} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
            await ctx.send(embed=embed)
            
            # Log the ban
            logger.info(f"User {member} was banned by {ctx.author} for reason: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to ban this member.")
        except Exception as e:
            await ctx.send("❌ An error occurred while trying to ban the member.")
            logger.error(f"Error in ban command: {e}")

    @commands.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int):
        """Clear messages in the channel (Requires manage messages permission)"""
        # Check if bot has permission to delete messages
        if not ctx.guild.me.guild_permissions.manage_messages:
            return await ctx.send("❌ I don't have permission to delete messages.")
            
        if amount <= 0 or amount > 100:
            return await ctx.send("❌ Please provide a number between 1 and 100.")
            
        try:
            deleted = await ctx.channel.purge(limit=amount + 1)  # +1 to include the command message
            await ctx.send(f"✅ Cleared {len(deleted) - 1} messages.", delete_after=5)
            
            # Log the clear
            logger.info(f"{ctx.author} cleared {len(deleted) - 1} messages in {ctx.channel}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to delete messages.")
        except Exception as e:
            await ctx.send("❌ An error occurred while trying to clear messages.")
            logger.error(f"Error in clear command: {e}")
    
    @commands.command(name="warn")
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason=None):
        """Warn a member (Requires kick permission)"""
        # Check if the target is higher in role hierarchy
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send("❌ You cannot warn someone with a higher or equal role.")
            
        # Convert IDs to strings for JSON compatibility
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        
        # Initialize guild in warns dict if it doesn't exist
        if guild_id not in self.warns:
            self.warns[guild_id] = {}
        
        # Initialize user in guild's warns dict if they don't exist
        if user_id not in self.warns[guild_id]:
            self.warns[guild_id][user_id] = []
        
        # Add the warning
        warning = {
            "reason": reason or "No reason provided",
            "timestamp": datetime.datetime.now().isoformat(),
            "moderator": str(ctx.author.id)
        }
        
        self.warns[guild_id][user_id].append(warning)
        self._save_warns()
        
        # Send confirmation
        embed = discord.Embed(
            title="User Warned",
            description=f"{member.mention} has been warned.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Reason", value=reason or "No reason provided")
        embed.add_field(name="Warning Count", value=str(len(self.warns[guild_id][user_id])))
        embed.set_footer(text=f"Warned by {ctx.author} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        await ctx.send(embed=embed)
        
        # DM the user
        try:
            user_embed = discord.Embed(
                title=f"Warning from {ctx.guild.name}",
                description=f"You have been warned.",
                color=discord.Color.gold()
            )
            user_embed.add_field(name="Reason", value=reason or "No reason provided")
            user_embed.add_field(name="Warning Count", value=str(len(self.warns[guild_id][user_id])))
            await member.send(embed=user_embed)
        except discord.Forbidden:
            await ctx.send("Note: Could not DM the user about their warning.")
        
        # Log the warning
        logger.info(f"User {member} was warned by {ctx.author} for reason: {reason}")
    
    @commands.command(name="warnings")
    @commands.has_permissions(kick_members=True)
    async def warnings(self, ctx, member: discord.Member):
        """View a member's warnings (Requires kick permission)"""
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        
        if guild_id not in self.warns or user_id not in self.warns[guild_id] or not self.warns[guild_id][user_id]:
            return await ctx.send(f"{member.display_name} has no warnings.")
        
        warnings = self.warns[guild_id][user_id]
        
        embed = discord.Embed(
            title=f"Warnings for {member.display_name}",
            description=f"Total warnings: {len(warnings)}",
            color=discord.Color.gold()
        )
        
        for i, warning in enumerate(warnings, 1):
            moderator = ctx.guild.get_member(int(warning["moderator"]))
            mod_name = moderator.display_name if moderator else "Unknown Moderator"
            
            embed.add_field(
                name=f"Warning {i}",
                value=f"**Reason:** {warning['reason']}\n**Moderator:** {mod_name}\n**Date:** {warning['timestamp'].split('T')[0]}",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="clearwarns")
    @is_admin()
    async def clearwarns(self, ctx, member: discord.Member):
        """Clear all warnings for a member (Requires administrator permission)"""
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        
        if guild_id in self.warns and user_id in self.warns[guild_id]:
            del self.warns[guild_id][user_id]
            self._save_warns()
            await ctx.send(f"✅ Cleared all warnings for {member.display_name}.")
            
            # Log the action
            logger.info(f"{ctx.author} cleared all warnings for {member}")
        else:
            await ctx.send(f"{member.display_name} has no warnings to clear.")
    
    @commands.command(name="announcement")
    @is_admin()
    async def announcement(self, ctx, channel: discord.TextChannel, *, message):
        """Make an announcement in the specified channel (Requires administrator permission)"""
        # Check if bot has permission to send messages in target channel
        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.")
            
        try:
            embed = discord.Embed(
                title="📢 Announcement",
                description=message,
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Announced by {ctx.author} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
            
            # Send the announcement
            sent_message = await channel.send(embed=embed)
            
            # Option to add a ping
            await ctx.send(f"Announcement posted in {channel.mention}. Would you like to add a ping? Reply with: `everyone`, `here`, `role @role`, or `none`")
            
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel
            
            try:
                reply = await bot.wait_for('message', check=check, timeout=30.0)
                
                # Check if bot has permission to mention everyone/here
                if (reply.content.lower() == 'everyone' or reply.content.lower() == 'here') and not channel.permissions_for(ctx.guild.me).mention_everyone:
                    await ctx.send("❌ I don't have permission to mention everyone/here in that channel.")
                    return
                
                if reply.content.lower() == 'everyone':
                    await channel.send("@everyone", allowed_mentions=discord.AllowedMentions(everyone=True))
                elif reply.content.lower() == 'here':
                    await channel.send("@here", allowed_mentions=discord.AllowedMentions(everyone=True))
                elif reply.content.lower().startswith('role '):
                    # Extract role mention
                    try:
                        role_id = int(reply.content.split('@')[1].split('>')[0])
                        role = ctx.guild.get_role(role_id)
                        if role:
                            # Check if the bot can mention the role
                            if not channel.permissions_for(ctx.guild.me).mention_everyone and role.is_default():
                                await ctx.send("❌ I don't have permission to mention that role.")
                                return
                                
                            await channel.send(f"{role.mention}", allowed_mentions=discord.AllowedMentions(roles=[role]))
                        else:
                            await ctx.send("❌ Role not found.")
                    except:
                        await ctx.send("❌ Invalid role format. No ping added.")
                elif reply.content.lower() != 'none':
                    await ctx.send("❌ Invalid option. No ping added.")
                
            except asyncio.TimeoutError:
                await ctx.send("No ping option selected within the time limit.")
            
            await ctx.send("✅ Announcement process completed.")
            
            # Log the announcement
            logger.info(f"{ctx.author} made an announcement in {channel}")
            
        except Exception as e:
            await ctx.send("❌ An error occurred while making the announcement.")
            logger.error(f"Error in announcement command: {e}")

# --- Utility Commands ---
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.polls = {}
        self.reminders = []
        self.load_data()  # Load data when the cog is initialized

    def load_data(self):
        # Load polls
        self.polls = load_from_json('polls.json', {})
        # Load reminders
        self.reminders = load_from_json('reminders.json', [])
    
    def save_data(self):
        # Save polls
        save_to_json(self.polls, 'polls.json')
        # Save reminders
        save_to_json(self.reminders, 'reminders.json')
    
    @commands.command(name="help")
    async def help_command(self, ctx, category=None):
        """Display bot commands and information"""
        
        # Define command categories with emojis and required permissions
        categories = {
            "music": {
                "emoji": "🎵",
                "title": "Music Commands",
                "description": "Commands for playing music in voice channels",
                "commands": {
                    "join": "Join your voice channel",
                    "play <song>": "Play a song from YouTube",
                    "queue": "View the current music queue",
                    "skip": "Skip the current song",
                    "stop": "Stop playback and clear the queue",
                    "leave": "Leave the voice channel"
                },
                "required_permissions": None  # No special permissions required
            },
            "moderation": {
                "emoji": "🛡️",
                "title": "Moderation Commands",
                "description": "Commands for server moderation (requires permissions)",
                "commands": {
                    "kick <member> [reason]": "Kick a member (Requires kick permission)",
                    "ban <member> [reason]": "Ban a member (Requires ban permission)",
                    "clear <amount>": "Clear messages (Requires manage messages permission)",
                    "warn <member> [reason]": "Warn a member (Requires kick permission)",
                    "warnings <member>": "View a member's warnings (Requires kick permission)",
                    "clearwarns <member>": "Clear a member's warnings (Requires administrator permission)",
                    "announcement <channel> <message>": "Make an announcement (Requires administrator permission)"
                },
                "required_permissions": discord.Permissions(kick_members=True)  # Basic mod permission check
            },
            "utility": {
                "emoji": "🔧",
                "title": "Utility Commands",
                "description": "Useful server utilities",
                "commands": {
                    "poll <question> <option1> <option2> ...": "Create a poll",
                    "remind <time> <reminder>": "Set a reminder (e.g., 1h, 30m, 2d)",
                    "serverinfo": "Display server information",
                    "userinfo [member]": "Display user information",
                    "avatar [member]": "Display a user's avatar",
                    "roll [dice]": "Roll dice (e.g., 2d6)",
                    "8ball <question>": "Ask the Magic 8-Ball"
                },
                "required_permissions": None  # No special permissions required
            },
            "fun": {
                "emoji": "🎮",
                "title": "Fun Commands",
                "description": "Commands for fun and entertainment",
                "commands": {
                    "meme": "Get a random meme",
                    "joke": "Get a random joke",
                    "quote": "Get an inspirational quote",
                    "choose <option1>, <option2>, ...": "Choose between options",
                    "fact": "Get a random fact"
                },
                "required_permissions": None  # No special permissions required
            }
        }
        
        # If no category specified, show main help menu
        if category is None:
            embed = discord.Embed(
                title="📚 Bot Help Menu",
                description="Here's a list of command categories. Use `!help <category>` to see specific commands.",
                color=discord.Color.blue()
            )
            
            # Add each category as a field
            for cat_name, cat_data in categories.items():
                # Check if user has permission to see this category
                if cat_data["required_permissions"] is None or ctx.author.guild_permissions >= cat_data["required_permissions"]:
                    embed.add_field(
                        name=f"{cat_data['emoji']} {cat_data['title']}",
                        value=f"{cat_data['description']}\nUse `!help {cat_name}` to see commands",
                        inline=False
                    )
                
            embed.set_footer(text="Type !help <category> for more info")
            
        # If category is specified, show category-specific help
        else:
            category = category.lower()
            if category in categories:
                cat_data = categories[category]
                
                # Check if user has permission to see this category
                if cat_data["required_permissions"] is not None and ctx.author.guild_permissions < cat_data["required_permissions"]:
                    return await ctx.send("❌ You don't have permission to view these commands.")
                
                embed = discord.Embed(
                    title=f"{cat_data['emoji']} {cat_data['title']}",
                    description=cat_data['description'],
                    color=discord.Color.blue()
                )
                
                # Add each command as a field
                for cmd, desc in cat_data['commands'].items():
                    embed.add_field(
                        name=f"!{cmd}",
                        value=desc,
                        inline=False
                    )
                    
                embed.set_footer(text="Type !help for main menu")
                
            else:
                # Category not found
                return await ctx.send(f"❌ Category '{category}' not found. Use `!help` to see available categories.")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="poll")
    async def poll(self, ctx, question, *options):
        """Create a poll with reactions"""
        if len(options) > 10:
            return await ctx.send("You can only have up to 10 options.")
        
        if len(options) < 2:
            return await ctx.send("You need at least 2 options.")
        
        # Create embed
        embed = discord.Embed(
            title="📊 Poll",
            description=question,
            color=discord.Color.blue()
        )
        
        # Add options with emojis
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        option_text = "\n".join([f"{emojis[i]} {option}" for i, option in enumerate(options)])
        
        embed.add_field(name="Options", value=option_text)
        embed.set_footer(text=f"Poll by {ctx.author} | React to vote!")
        
        # Send poll
        poll_message = await ctx.send(embed=embed)
        
        # Add reactions
        for i in range(len(options)):
            await poll_message.add_reaction(emojis[i])
        
        # Save poll data
        self.polls[str(poll_message.id)] = {
            "question": question,
            "options": list(options),
            "emojis": emojis[:len(options)],
            "channel_id": ctx.channel.id,
            "author_id": ctx.author.id,
            "created_at": datetime.datetime.now().isoformat()
        }
        self.save_data()
    
    @commands.command(name="remind")
    async def remind(self, ctx, time, *, reminder):
        """Set a reminder for later"""
        # Parse time (format: 1h, 30m, 2d, etc.)
        try:
            time_value = int(time[:-1])
            time_unit = time[-1].lower()
            
            if time_value <= 0:
                return await ctx.send("Time value must be positive.")
                
            if time_unit == 'm':
                seconds = time_value * 60
                time_str = f"{time_value} minute(s)"
            elif time_unit == 'h':
                seconds = time_value * 3600
                time_str = f"{time_value} hour(s)"
            elif time_unit == 'd':
                seconds = time_value * 86400
                time_str = f"{time_value} day(s)"
            else:
                return await ctx.send("Invalid time format. Use `1m`, `2h`, or `3d` format.")
        except ValueError:
            return await ctx.send("Invalid time format. Use `1m`, `2h`, or `3d` format.")
        
        # Calculate reminder time
        reminder_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        
        # Add reminder to list
        self.reminders.append({
            "user_id": ctx.author.id,
            "channel_id": ctx.channel.id,
            "reminder": reminder,
            "reminder_time": reminder_time.isoformat()
        })
        self.save_data()
        
        # Confirm
        await ctx.send(f"I'll remind you about `{reminder}` in {time_str}.")
    
    @tasks.loop(minutes=1)
    async def check_reminders(self):
        """Check for reminders that need to be sent"""
        now = datetime.datetime.now()
        reminders_to_send = []
        reminders_to_keep = []
        
        for reminder in self.reminders:
            reminder_time = datetime.datetime.fromisoformat(reminder["reminder_time"])
            
            if reminder_time <= now:
                reminders_to_send.append(reminder)
            else:
                reminders_to_keep.append(reminder)
        
        # Update reminders list
        if len(reminders_to_send) > 0:
            self.reminders = reminders_to_keep
            self.save_data()
        
        # Send reminders
        for reminder in reminders_to_send:
            try:
                user = await self.bot.fetch_user(reminder["user_id"])
                channel = self.bot.get_channel(reminder["channel_id"])
                
                if user and channel:
                    embed = discord.Embed(
                        title="⏰ Reminder",
                        description=reminder["reminder"],
                        color=discord.Color.purple()
                    )
                    embed.set_footer(text="Reminder set on: " + reminder["reminder_time"].split("T")[0])
                    
                    await channel.send(f"{user.mention}", embed=embed)
            except Exception as e:
                logger.error(f"Error sending reminder: {e}")
    
    def cog_unload(self):
        """Save data when the cog is unloaded"""
        self.check_reminders.cancel()
        self.save_data()

    @check_reminders.before_loop
    async def before_check_reminders(self):
        """Wait for the bot to be ready before starting the reminder loop"""
        await self.bot.wait_until_ready()

    @commands.command(name="serverinfo")
    async def serverinfo(self, ctx):
        """Display information about the server"""
        guild = ctx.guild
        
        # Count channels by type
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        
        # Count roles (excluding @everyone)
        roles = len(guild.roles) - 1
        
        # Create embed
        embed = discord.Embed(
            title=f"{guild.name} Server Information",
            color=guild.me.color
        )
        
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        # Add general info
        embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
        embed.add_field(name="Server ID", value=guild.id, inline=True)
        embed.add_field(name="Created On", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
        
        # Add member info
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Boost Level", value=f"Level {guild.premium_tier}", inline=True)
        embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
        
        # Add channel info
        embed.add_field(name="Channels", value=f"Text: {text_channels}\nVoice: {voice_channels}\nCategories: {categories}", inline=True)
        embed.add_field(name="Roles", value=roles, inline=True)
        embed.add_field(name="Emojis", value=len(guild.emojis), inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name="userinfo")
    async def userinfo(self, ctx, member: discord.Member = None):
        """Display information about a user"""
        if member is None:
            member = ctx.author
        
        # Calculate join position
        join_pos = sorted(ctx.guild.members, key=lambda m: m.joined_at).index(member) + 1
        
        # Create embed
        embed = discord.Embed(
            title=f"User Information: {member.display_name}",
            color=member.color
        )
        
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        
        # Add user info
        embed.add_field(name="Username", value=str(member), inline=True)
        embed.add_field(name="User ID", value=member.id, inline=True)
        embed.add_field(name="Status", value=str(member.status).title(), inline=True)
        
        # Add dates
        embed.add_field(
            name="Account Created",
            value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            inline=True
        )
        embed.add_field(
            name="Joined Server",
            value=f"{member.joined_at.strftime('%Y-%m-%d %H:%M:%S')}\n(#{join_pos})",
            inline=True
        )
        
        # Add roles (if they fit)
        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        if roles:
            roles_text = " ".join(roles)
            if len(roles_text) >= 1024:
                roles_text = f"{len(roles)} roles"
            embed.add_field(
                name=f"Roles [{len(roles)}]",
                value=roles_text,
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.command(name="avatar")
    async def avatar(self, ctx, member: discord.Member = None):
        """Display a user's avatar"""
        if member is None:
            member = ctx.author
        
        embed = discord.Embed(
            title=f"{member.display_name}'s Avatar",
            color=member.color
        )
        
        if member.avatar:
            embed.set_image(url=member.avatar.url)
        else:
            embed.set_image(url=member.default_avatar.url)
            
        await ctx.send(embed=embed)
    
    @commands.command(name="roll")
    async def roll(self, ctx, dice: str = "1d6"):
        """Roll dice in NdN format"""
        try:
            rolls, limit = map(int, dice.split('d'))
        except ValueError:
            return await ctx.send("Format has to be NdN!")
        
        if rolls > 25:
            return await ctx.send("You can roll a maximum of 25 dice at once.")
        
        if limit > 1000:
            return await ctx.send("The maximum number of sides per die is 1000.")
        
        if rolls < 1 or limit < 1:
            return await ctx.send("Both values must be positive!")
        
        results = [random.randint(1, limit) for _ in range(rolls)]
        
        # Create embed
        embed = discord.Embed(
            title="🎲 Dice Roll",
            description=f"Rolling {dice}",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="Results",
            value=", ".join(str(r) for r in results),
            inline=False
        )
        
        embed.add_field(
            name="Total",
            value=str(sum(results)),
            inline=True
        )
        
        embed.set_footer(text=f"Rolled by {ctx.author}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="8ball")
    async def eight_ball(self, ctx, *, question):
        """Ask the Magic 8-Ball a question"""
        responses = [
            "It is certain.",
            "It is decidedly so.",
            "Without a doubt.",
            "Yes - definitely.",
            "You may rely on it.",
            "As I see it, yes.",
            "Most likely.",
            "Outlook good.",
            "Yes.",
            "Signs point to yes.",
            "Reply hazy, try again.",
            "Ask again later.",
            "Better not tell you now.",
            "Cannot predict now.",
            "Concentrate and ask again.",
            "Don't count on it.",
            "My reply is no.",
            "My sources say no.",
            "Outlook not so good.",
            "Very doubtful."
        ]
        
        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            color=discord.Color.purple()
        )
        
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=random.choice(responses), inline=False)
        embed.set_footer(text=f"Asked by {ctx.author}")
        
        await ctx.send(embed=embed)

# --- Fun Commands ---
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def _fetch_api_data(self, url, headers=None):
        """Helper method to fetch data from APIs with error handling"""
        default_headers = {"User-agent": "Discord Bot"}
        if headers:
            default_headers.update(headers)
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=default_headers, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"API error: {response.status} from {url}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching data from {url}: {e}")
            return None
    
    @commands.command(name="meme")
    async def meme(self, ctx):
        """Fetches a random meme from Reddit"""
        # Safe list of subreddits
        subreddits = ["memes", "dankmemes", "wholesomememes"]
        subreddit = random.choice(subreddits)
        
        # Use the async helper method
        data = await self._fetch_api_data(f"https://www.reddit.com/r/{subreddit}/hot.json?limit=100")
        if not data:
            return await ctx.send("Couldn't fetch memes right now. Try again later.")
            
        # Filter posts for safety
        posts = [post for post in data["data"]["children"] 
                if not post["data"].get("is_self", True) 
                and not post["data"].get("over_18", True)
                and post["data"].get("url")]
        
        if not posts:
            return await ctx.send("Couldn't find any suitable memes right now. Try again later.")
        
        random_post = random.choice(posts)
        post_data = random_post["data"]
        
        # Sanitize data before displaying
        title = post_data.get("title", "Untitled Meme")[:256]  # Limit title length
        permalink = post_data.get("permalink", "")
        url = post_data.get("url", "")
        ups = post_data.get("ups", 0)
        comments = post_data.get("num_comments", 0)
        
        # Verify URL is actually an image
        if not url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            return await ctx.send("Found a meme but it wasn't an image. Try again!")
        
        embed = discord.Embed(
            title=title,
            url=f"https://reddit.com{permalink}" if permalink else None,
            color=discord.Color.orange()
        )
        
        embed.set_image(url=url)
        embed.set_footer(text=f"👍 {ups} | 💬 {comments} | From r/{subreddit}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="joke")
    async def joke(self, ctx):
        """Tells a random joke"""
        data = await self._fetch_api_data("https://official-joke-api.appspot.com/random_joke")
        if not data:
            return await ctx.send("Failed to fetch a joke. Try again later.")
            
        # Sanitize data
        setup = data.get("setup", "")
        punchline = data.get("punchline", "")
        
        if not setup or not punchline:
            return await ctx.send("Received an invalid joke. Try again later.")
            
        joke = f"{setup}\n\n{punchline}"
        
        embed = discord.Embed(
            title="😂 Random Joke",
            description=joke[:4000],  # Limit description length
            color=discord.Color.gold()
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="quote")
    async def quote(self, ctx):
        """Fetches a random inspirational quote"""
        data = await self._fetch_api_data("https://api.quotable.io/random")
        if not data:
            return await ctx.send("Failed to fetch a quote. Try again later.")
        
        # Sanitize data
        content = data.get("content", "")
        author = data.get("author", "Unknown")
        
        if not content:
            return await ctx.send("Received an invalid quote. Try again later.")
            
        embed = discord.Embed(
            title="📜 Inspirational Quote",
            description=f"\"{content[:1000]}\"",  # Limit quote length
            color=discord.Color.teal()
        )
        
        embed.set_footer(text=f"- {author[:100]}")  # Limit author length
        
        await ctx.send(embed=embed)
    
    @commands.command(name="choose")
    async def choose(self, ctx, *, options):
        """Choose between multiple options separated by commas"""
        # Protect against injection or extremely long inputs
        if len(options) > 1000:
            return await ctx.send("That's too many options! Please keep your input shorter.")
            
        option_list = [option.strip() for option in options.split(",") if option.strip()]
        
        if len(option_list) < 2:
            return await ctx.send("Please provide at least two options separated by commas.")
        
        # Limit individual option length for display
        option_list = [opt[:100] for opt in option_list]
        choice = random.choice(option_list)
        
        embed = discord.Embed(
            title="🤔 Choice Made",
            description=f"I choose: **{choice}**",
            color=discord.Color.blue()
        )
        
        # Safely get author name
        author_name = str(ctx.author)[:100]  # Limit length
        embed.set_footer(text=f"Requested by {author_name}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name="fact")
    async def fact(self, ctx):
        """Shares a random fact"""
        facts = [
            "The shortest war in history was between Britain and Zanzibar on August 27, 1896. Zanzibar surrendered after 38 minutes.",
            "A group of flamingos is called a 'flamboyance'.",
            "The Eiffel Tower can be 15 cm taller during the summer due to thermal expansion.",
            "Honey never spoils. Archaeologists have found pots of honey in ancient Egyptian tombs that are over 3,000 years old and still perfectly good to eat.",
            "A day on Venus is longer than a year on Venus. It takes 243 Earth days to rotate once on its axis (a day) and 225 Earth days to complete one orbit of the sun (a year).",
            "The fingerprints of koalas are so indistinguishable from humans that they have occasionally been confused at crime scenes.",
            "The Hawaiian alphabet has only 12 letters.",
            "A strawberry isn't actually a berry, but a banana is.",
            "Cows have best friends and get stressed when they are separated.",
            "The average person will spend six months of their life waiting for red lights to turn green.",
            "The world's oldest known living tree is a Great Basin Bristlecone Pine that is over 5,000 years old.",
            "A hummingbird weighs less than a penny.",
            "It's impossible to hum while holding your nose closed.",
            "The total weight of all ants on Earth is greater than the total weight of all humans.",
            "Octopuses have three hearts.",
            "The tallest mountain in our solar system is Olympus Mons on Mars, which is almost three times the height of Mount Everest.",
            "A cockroach can live for several weeks without its head."
        ]
        
        embed = discord.Embed(
            title="🧠 Random Fact",
            description=random.choice(facts),
            color=discord.Color.dark_blue()
        )
        
        await ctx.send(embed=embed)

    # Add error handling for all commands
    @meme.error
    @joke.error
    @quote.error
    @fact.error
    async def fun_command_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"This command is on cooldown. Try again in {error.retry_after:.1f} seconds.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("You're missing required arguments for this command.")
        else:
            await ctx.send("An error occurred while processing this command.")
            logger.error(f"Error in fun command: {str(error)}")
    
    @choose.error
    async def choose_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Please provide options to choose from, separated by commas.")
        else:
            await ctx.send("An error occurred while processing this command.")
            logger.error(f"Error in choose command: {str(error)}")

# Register all cogs with the bot
bot.add_cog(MusicCog(bot))
bot.add_cog(ModerationCog(bot))
bot.add_cog(UtilityCog(bot))
bot.add_cog(FunCog(bot))

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