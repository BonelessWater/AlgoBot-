# Discord Multi-Purpose Bot

A feature-rich Discord bot that provides music playback, moderation tools, utility commands, and fun features for your server.

## Features

### 🎵 Music
- Play music from YouTube
- Queue system for multiple tracks
- Skip, stop, and leave commands
- View current queue

### 🛡️ Moderation
- Kick and ban users
- Clear messages in bulk
- Warning system with tracking
- Make announcements with optional pings

### 🔧 Utility
- Create polls with reactions
- Set reminders for later
- View server and user information
- Roll dice and other random utilities

### 🎮 Fun
- Random memes from Reddit
- Jokes and inspirational quotes
- Magic 8-ball responses
- Random facts and choices

## Commands

### Music Commands
- `!join` - Join your voice channel
- `!play <song>` - Play a song from YouTube
- `!queue` - View the current music queue
- `!skip` - Skip the current song
- `!stop` - Stop playback and clear the queue
- `!leave` - Leave the voice channel

### Moderation Commands
- `!kick <member> [reason]` - Kick a member
- `!ban <member> [reason]` - Ban a member
- `!clear <amount>` - Clear messages
- `!warn <member> [reason]` - Warn a member
- `!warnings <member>` - View a member's warnings
- `!clearwarns <member>` - Clear a member's warnings
- `!announcement <channel> <message>` - Make an announcement

### Utility Commands
- `!poll <question> <option1> <option2> ...` - Create a poll
- `!remind <time> <reminder>` - Set a reminder (e.g., 1h, 30m, 2d)
- `!serverinfo` - Display server information
- `!userinfo [member]` - Display user information
- `!avatar [member]` - Display a user's avatar
- `!roll [dice]` - Roll dice (e.g., 2d6)
- `!8ball <question>` - Ask the Magic 8-Ball

### Fun Commands
- `!meme` - Get a random meme
- `!joke` - Get a random joke
- `!quote` - Get an inspirational quote
- `!choose <option1>, <option2>, ...` - Choose between options
- `!fact` - Get a random fact

## Setup

1. Clone the repository
2. Install the required dependencies:
   ```
   pip install discord.py youtube_dl python-dotenv requests
   ```
3. Create a `.env` file with your Discord bot token:
   ```
   TOKEN=your_discord_bot_token
   ```
4. Run the bot:
   ```
   python bot.py
   ```

## Requirements
- Python 3.6 or higher
- discord.py
- youtube_dl
- python-dotenv
- requests

## Note
This bot includes a health check server on port 8000 for monitoring, useful when deployed to services like Azure App Service.