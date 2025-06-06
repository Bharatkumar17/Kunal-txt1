import os
import re
import sys
import json
import time
import asyncio
import requests
import subprocess
import logging
from utils import progress_bar
import core as helper
from config import BOT_TOKEN, API_ID, API_HASH, MONGO_URI, BOT_NAME
import aiohttp
from aiohttp import ClientSession
from pyromod import listen
from subprocess import getstatusoutput
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait, ChatIdInvalid
from pyrogram.errors.exceptions.bad_request_400 import StickerEmojiInvalid
from pyrogram.types.messages_and_media import message
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from bs4 import BeautifulSoup
from logs import get_last_two_minutes_logs
import tempfile
from db import get_collection, save_name, load_name, save_log_channel_id, load_log_channel_id, save_authorized_users, load_authorized_users, load_allowed_channel_ids, save_allowed_channel_ids, load_accept_logs, save_accept_logs
from db import save_bot_running_time, load_bot_running_time, reset_bot_running_time, save_max_running_time, load_max_running_time
from db import save_queue_file, load_queue_file
from PIL import Image
from pytube import Playlist
from yt_dlp import YoutubeDL
import yt_dlp as youtube_dl

# Initialize the bot
bot = Client(
    "bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Get the MongoDB collection for this bot
collection = get_collection(BOT_NAME, MONGO_URI)

# Constants
OWNER_IDS = [1012164907]  # Replace with actual owner ID

# Global variables with safe defaults
log_channel_id = None  # Initialize as None
authorized_users = [1012164907]
ALLOWED_CHANNEL_IDS = []  # Start empty
my_name = "BHARAT❤️"
overlay = None 
accept_logs = 0
bot_running = False
start_time = None
total_running_time = None
max_running_time = None
file_queue = []

# Load initial data from files
def load_initial_data():
    global log_channel_id, authorized_users, ALLOWED_CHANNEL_IDS, my_name, accept_logs
    global total_running_time, max_running_time
  
    log_channel_id = load_log_channel_id(collection)
    authorized_users = load_authorized_users(collection)
    ALLOWED_CHANNEL_IDS = load_allowed_channel_ids(collection)
    my_name = load_name(collection)
    accept_logs = load_accept_logs(collection)
    total_running_time = load_bot_running_time(collection)
    max_running_time = load_max_running_time(collection)
    file_queue = load_queue_file(collection)

# ===================== SAFE CHANNEL ACCESS =====================
async def safe_send_message(chat_id, text, **kwargs):
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except (ChatIdInvalid, ValueError) as e:
        logging.error(f"Failed to send to {chat_id}: {str(e)}")
        return None

async def safe_send_document(chat_id, document, **kwargs):
    try:
        return await bot.send_document(chat_id, document, **kwargs)
    except (ChatIdInvalid, ValueError) as e:
        logging.error(f"Failed to send doc to {chat_id}: {str(e)}")
        return None

# ===================== CHANNEL VERIFICATION =====================
async def verify_channel_access(chat_id):
    """Check if bot has access to a channel"""
    try:
        await bot.get_chat(chat_id)
        return True
    except Exception as e:
        logging.error(f"Channel access failed for {chat_id}: {str(e)}")
        return False

# ===================== STARTUP CHECKS =====================
@bot.on_start()
async def startup_checks(_, __):
    global log_channel_id, ALLOWED_CHANNEL_IDS
    
    # Verify log channel
    if log_channel_id:
        if not await verify_channel_access(log_channel_id):
            logging.warning(f"Log channel {log_channel_id} inaccessible")
            log_channel_id = None
    
    # Filter valid channels
    valid_channels = []
    for channel_id in ALLOWED_CHANNEL_IDS:
        if await verify_channel_access(channel_id):
            valid_channels.append(channel_id)
        else:
            logging.warning(f"Removed invalid channel: {channel_id}")
    
    ALLOWED_CHANNEL_IDS = valid_channels
    save_allowed_channel_ids(collection, ALLOWED_CHANNEL_IDS)

# Filters
def owner_filter(_, __, message):
    return bool(message.from_user and message.from_user.id in OWNER_IDS)

def channel_filter(_, __, message):
    return bool(message.chat and message.chat.id in ALLOWED_CHANNEL_IDS)

def auth_user_filter(_, __, message):
    return bool(message.from_user and message.from_user.id in authorized_users)

auth_or_owner_filter = filters.create(lambda _, __, m: auth_user_filter(_, __, m) or owner_filter(_, __, m))
auth_owner_channel_filter = filters.create(lambda _, __, m: auth_user_filter(_, __, m) or owner_filter(_, __, m) or channel_filter(_, __, m))
owner_or_channel_filter = filters.create(lambda _, __, m: owner_filter(_, __, m) or channel_filter(_, __, m))

# ===================== MODIFIED COMMAND HANDLERS =====================
@bot.on_message(filters.command("add_log_channel") & filters.create(owner_filter))
async def add_log_channel(client: Client, message: Message):
    global log_channel_id
    try:
        new_log_channel_id = int(message.text.split(maxsplit=1)[1])
        
        # Verify channel access before setting
        if await verify_channel_access(new_log_channel_id):
            log_channel_id = new_log_channel_id
            save_log_channel_id(collection, log_channel_id)
            await message.reply(f"✅ Log channel ID updated to {new_log_channel_id}.")
        else:
            await message.reply("❌ Bot can't access this channel. Add bot first!")
    except (IndexError, ValueError):
        await message.reply("⚠️ Please provide a valid channel ID.")

@bot.on_message(filters.command("add_channel") & auth_or_owner_filter)
async def add_channel(client: Client, message: Message):
    global ALLOWED_CHANNEL_IDS
    try:
        new_channel_id = int(message.text.split(maxsplit=1)[1])
        
        if not await verify_channel_access(new_channel_id):
            await message.reply("❌ Bot can't access this channel. Add bot first!")
            return
            
        if new_channel_id not in ALLOWED_CHANNEL_IDS:
            ALLOWED_CHANNEL_IDS.append(new_channel_id)
            save_allowed_channel_ids(collection, ALLOWED_CHANNEL_IDS)
            await message.reply(f"✅ Channel {new_channel_id} added to allowed channels.")
        else:
            await message.reply(f"ℹ️ Channel {new_channel_id} is already in the allowed channels list.")
    except (IndexError, ValueError):
        await message.reply("⚠️ Please provide a valid channel ID.")

@bot.on_message(filters.command("show_channels") & auth_or_owner_filter)
async def show_channels(client: Client, message: Message):
    if ALLOWED_CHANNEL_IDS:
        channels_list = "\n".join([f"• {channel_id}" for channel_id in ALLOWED_CHANNEL_IDS])
        await message.reply(f"📢 Allowed channels:\n{channels_list}")
    else:
        await message.reply("ℹ️ No channels are currently allowed.")

# ===================== MAIN DOWNLOAD HANDLER =====================
@bot.on_message(filters.command(["bharat"]))
async def luminant_command(bot: Client, m: Message):
    global bot_running, start_time, total_running_time, max_running_time
    global log_channel_id, my_name, overlay, accept_logs
    
    # Check if log channel is accessible
    if log_channel_id and not await verify_channel_access(log_channel_id):
        log_channel_id = None
        await m.reply("⚠️ Log channel inaccessible. Disabled logging.")
    
    await m.delete()
    chat_id = m.chat.id
    
    if bot_running:
        running_message = await m.reply_text("⚙️ Process is already running. Queue your request? (yes/no)")
        input_queue: Message = await bot.listen(chat_id)
        response = input_queue.text.strip().lower()
        await input_queue.delete()
        await running_message.delete()

        if response != "yes":
            await m.reply_text("❌ Process not queued.")
            return

    editable = await m.reply_text("📄 Send your .txt file:")
    input: Message = await bot.listen(editable.chat.id)
    
    if input.document:
        x = await input.download()        
        if log_channel_id:
            await safe_send_document(log_channel_id, x)                    
        await input.delete(True)
        file_name, ext = os.path.splitext(os.path.basename(x))
        credit = my_name

        path = f"./downloads/{m.chat.id}"

        try:
            with open(x, "r") as f:
                content = f.read()
            content = content.split("\n")
            links = []
            for i in content:
                links.append(i.split("://", 1))
            os.remove(x)
        except Exception as e:
            await m.reply_text(f"❌ Invalid file input: {str(e)}")
            os.remove(x)
            bot_running = False
            return
    else:
        content = input.text
        content = content.split("\n")
        links = []
        for i in content:
            links.append(i.split("://", 1))

    # ... [REST OF THE DOWNLOAD LOGIC REMAINS THE SAME] ...

    # Use safe_send_message instead of direct send_message
    await safe_send_message(
        log_channel_id if log_channel_id else m.chat.id,
        f"**•File name** - `{b_name}`\n**•Total Links** - `{len(links)}`\n**•Range** - `({count}-{end_count})`"
    )

    # ... [CONTINUE WITH PROCESSING] ...

# ===================== HELP COMMANDS =====================
help_text = """
🤖 **Bot Commands Guide**

🔑 **Admin Commands:**
- /add_channel <id> - Add allowed channel
- /remove_channel <id> - Remove channel
- /show_channels - List allowed channels

📥 **Download Commands:**
- /bharat - Start download process
- /youtube - Extract YouTube links
- /h2t - Convert HTML to TXT

⚙️ **Settings:**
- /watermark - Set watermark
- /logs - Get bot logs
- /name - Set your name

ℹ️ **Info:**
- /start - Welcome message
- /help - This menu
"""

@bot.on_message(filters.command("help") & auth_owner_channel_filter)
async def help_command(client: Client, message: Message):
    await safe_send_message(
        message.chat.id,
        help_text,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Add Chat", callback_data="add_chat"),
              InlineKeyboardButton("Remove Chat", callback_data="remove_chat")]]
        )
    )

# ===================== BOT STARTUP =====================
load_initial_data()
bot.run()
