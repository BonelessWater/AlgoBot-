import os
import logging
import threading
import random
import asyncio
import datetime
import json
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
intents.members = True  # Enable member intents for welcome messages

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
    
    # Start background tasks
    status_updater.start()
    
    # Load polls and reminders if they exist
    utility_cog = bot.get_cog("UtilityCog")
    if utility_cog:
        utility_cog.load_data()
        utility_cog.check_reminders.start()

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

# Welcome new members
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

# --- Music Commands ---
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}
        self.now_playing = {}

    @commands.command(name="join")
    async def join(self, ctx):
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            await channel.connect()
            await ctx.send(f"Joined {channel.name}.")
        else:
            await ctx.send("You're not connected to a voice channel.")

    @commands.command(name="play")
    async def play(self, ctx, *, query: str):
        if ctx.voice_client is None:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
                await channel.connect()
            else:
                await ctx.send("You're not connected to a voice channel.")
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
            await ctx.send("An error occurred while trying to play the track.")
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
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("Skipped the current song.")
        else:
            await ctx.send("No audio is playing.")

    @commands.command(name="stop")
    async def stop(self, ctx):
        guild_id = ctx.guild.id
        if ctx.voice_client and ctx.voice_client.is_playing():
            # Clear the queue
            self.queue[guild_id] = []
            self.now_playing.pop(guild_id, None)
            
            # Stop playback
            ctx.voice_client.stop()
            await ctx.send("Playback stopped and queue cleared.")
        else:
            await ctx.send("No audio is playing.")

    @commands.command(name="leave")
    async def leave(self, ctx):
        guild_id = ctx.guild.id
        # Clear queue and now playing
        self.queue[guild_id] = []
        self.now_playing.pop(guild_id, None)
        
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("Left the voice channel.")
        else:
            await ctx.send("I'm not connected to a voice channel.")

# --- Moderation Commands ---
class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.warns = {}
        # Load warnings from file if it exists
        try:
            with open('warns.json', 'r') as f:
                self.warns = json.load(f)
        except FileNotFoundError:
            pass

    def _save_warns(self):
        with open('warns.json', 'w') as f:
            json.dump(self.warns, f)

    @commands.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int):
        try:
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(f"Cleared {len(deleted)} messages.", delete_after=5)
        except Exception as e:
            await ctx.send("An error occurred while trying to clear messages.")
            logger.error(f"Error in clear command: {e}")
     
    @commands.command(name="announcement")
    @commands.has_permissions(administrator=True)
    async def announcement(self, ctx, channel: discord.TextChannel, *, message):
        """Make an announcement in the specified channel (Admin only)"""
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
                            await channel.send(f"{role.mention}", allowed_mentions=discord.AllowedMentions(roles=[role]))
                        else:
                            await ctx.send("Role not found.")
                    except:
                        await ctx.send("Invalid role format. No ping added.")
                elif reply.content.lower() != 'none':
                    await ctx.send("Invalid option. No ping added.")
                
            except asyncio.TimeoutError:
                await ctx.send("No ping option selected within the time limit.")
            
            await ctx.send("Announcement process completed.")
            
        except Exception as e:
            await ctx.send("An error occurred while making the announcement.")
            logger.error(f"Error in announcement command: {e}")

# --- Utility Commands ---
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.polls = {}
        self.reminders = []

    @commands.command(name="help")
    async def help_command(self, ctx, category=None):
        """Display bot commands and information"""
        
        # Define command categories with emojis
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
                }
            },
            "moderation": {
                "emoji": "🛡️",
                "title": "Moderation Commands",
                "description": "Commands for server moderation",
                "commands": {
                    "clear <amount>": "Clear messages",
                    "announcement <channel> <message>": "Make an announcement"
                }
            },
            "utility": {
                "emoji": "🔧",
                "title": "Utility Commands",
                "description": "Useful server utilities",
                "commands": {
                    "poll <question> <option1> <option2> ...": "Create a poll",
                    "serverinfo": "Display server information",
                    "userinfo [member]": "Display user information",
                    "avatar [member]": "Display a user's avatar",
                }
            },
            "fun": {
                "emoji": "🎮",
                "title": "Fun Commands",
                "description": "Commands for fun and entertainment",
                "commands": {
                    "meme": "Get a random meme",
                    "quote": "Get an inspirational quote",
                }
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
                embed.add_field(
                    name=f"{cat_data['emoji']} {cat_data['title']}",
                    value=f"{cat_data['description']}\nUse `!help {cat_name}` to see commands",
                    inline=False
                )
                
            embed.set_footer(text="Bot created by Your Name • Type !help <category> for more info")
            
        # If category is specified, show category-specific help
        else:
            category = category.lower()
            if category in categories:
                cat_data = categories[category]
                
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
                    
                embed.set_footer(text="Bot created by Your Name • Type !help for main menu")
                
            else:
                # Category not found
                return await ctx.send(f"Category '{category}' not found. Use `!help` to see available categories.")
        
        await ctx.send(embed=embed)
        
    def load_data(self):
        # Load polls
        try:
            with open('polls.json', 'r') as f:
                self.polls = json.load(f)
        except FileNotFoundError:
            self.polls = {}
        
        # Load reminders
        try:
            with open('reminders.json', 'r') as f:
                self.reminders = json.load(f)
        except FileNotFoundError:
            self.reminders = []
    
    def save_data(self):
        # Save polls
        with open('polls.json', 'w') as f:
            json.dump(self.polls, f)
        
        # Save reminders
        with open('reminders.json', 'w') as f:
            json.dump(self.reminders, f)
    
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
        option_text = ""
        
        for i, option in enumerate(options):
            option_text += f"{emojis[i]} {option}\n"
        
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
    
    @tasks.loop(minutes=1)
    async def check_reminders(self):
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
        
        # Add roles
        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        if roles:
            embed.add_field(
                name=f"Roles [{len(roles)}]",
                value=" ".join(roles) if len(" ".join(roles)) < 1024 else f"{len(roles)} roles",
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
            await ctx.send(embed=embed)
        else:
            embed.set_image(url=member.default_avatar.url)
            await ctx.send(embed=embed)
    
# --- Fun Commands ---
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="meme")
    async def meme(self, ctx):
        """Fetches a random meme from Reddit"""
        subreddits = ["memes", "dankmemes", "wholesomememes"]
        subreddit = random.choice(subreddits)
        
        try:
            response = requests.get(f"https://www.reddit.com/r/{subreddit}/hot.json?limit=100", 
                                   headers={"User-agent": "Discord Bot"})
            data = response.json()
            
            posts = [post for post in data["data"]["children"] 
                    if not post["data"]["is_self"] and not post["data"]["over_18"]]
            
            if not posts:
                return await ctx.send("Couldn't find any memes right now. Try again later.")
            
            random_post = random.choice(posts)
            post_data = random_post["data"]
            
            embed = discord.Embed(
                title=post_data["title"],
                url=f"https://reddit.com{post_data['permalink']}",
                color=discord.Color.orange()
            )
            
            embed.set_image(url=post_data["url"])
            embed.set_footer(text=f"👍 {post_data['ups']} | 💬 {post_data['num_comments']} | From r/{subreddit}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send("Failed to fetch a meme. Try again later.")
            logger.error(f"Error in meme command: {e}")
    
    @commands.command(name="quote")
    async def quote(self, ctx):
        """Fetches a random inspirational quote"""
        try:
            response = requests.get("https://api.quotable.io/random")
            data = response.json()
            
            embed = discord.Embed(
                title="📜 Inspirational Quote",
                description=f"\"{data['content']}\"",
                color=discord.Color.teal()
            )
            
            embed.set_footer(text=f"- {data['author']}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send("Failed to fetch a quote. Try again later.")
            logger.error(f"Error in quote command: {e}")

async def setup_bot():
    await bot.add_cog(MusicCog(bot))
    await bot.add_cog(ModerationCog(bot))
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(FunCog(bot))

@bot.event
async def on_ready():
    try:
        await setup_bot()
        await bot.tree.sync()
    except Exception as e:
        logger.error("Error in setup: %s", e)
    logger.info("Logged in as %s", bot.user)
    
    # Start background tasks
    status_updater.start()
    
    # Load polls and reminders if they exist
    utility_cog = bot.get_cog("UtilityCog")
    if utility_cog:
        utility_cog.load_data()
        utility_cog.check_reminders.start()

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