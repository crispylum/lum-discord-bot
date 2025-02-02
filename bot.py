import discord
import openai
import os
import sqlite3
import re
import requests  # For Giphy API requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY")  # Ensure this is set in your .env

# Initialize OpenAI API
openai.api_key = OPENAI_API_KEY

# Intents for reading messages and fetching members
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True  # Allows bot to see members

client = discord.Client(intents=intents)

# SQLite Database setup (Permanent Memory)
DB_FILE = "chat_memory.db"

def setup_database():
    """Ensure database tables are correctly created (without dropping them)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Table for user memory
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS user_memory (
            user_id INTEGER,
            memory_key TEXT,
            memory_value TEXT,
            PRIMARY KEY (user_id, memory_key)
        )"""
    )
    # Table for Lum's opinions/preferences (also used for language setting)
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS lum_preferences (
            preference_key TEXT PRIMARY KEY,
            preference_value TEXT
        )"""
    )
    # Table for conversation history
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    # Table for allowed channels
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS allowed_channels (
            channel_id INTEGER PRIMARY KEY,
            channel_name TEXT
        )"""
    )
    conn.commit()
    conn.close()

setup_database()  # Initialize database

# --- Permanent Memory Functions ---

def set_user_memory(user_id, key, value):
    """Store or update any fact about a user."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_memory (user_id, memory_key, memory_value) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, memory_key) DO UPDATE SET memory_value = ?",
        (user_id, key, value, value)
    )
    conn.commit()
    conn.close()

def get_user_memory(user_id, key):
    """Retrieve any fact Lum remembers about a user."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT memory_value FROM user_memory WHERE user_id = ? AND memory_key = ?", (user_id, key))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# Lum's opinions/preferences functions (also used for language)
def set_lum_preference(key, value):
    """Store or update Lum's opinion or preference permanently."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO lum_preferences (preference_key, preference_value) VALUES (?, ?) "
        "ON CONFLICT(preference_key) DO UPDATE SET preference_value = ?",
        (key, value, value)
    )
    conn.commit()
    conn.close()

def get_lum_preference(key):
    """Retrieve Lum's opinion (or setting) on a given subject."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT preference_value FROM lum_preferences WHERE preference_key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# Conversation history functions
def add_conversation_message(user_id, role, content):
    """Append a message (user or assistant) to the permanent conversation history."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversation_history (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()
    conn.close()

def get_conversation_history(user_id, limit=10):
    """
    Retrieve the last `limit` messages for the user in chronological order.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM conversation_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    rows.reverse()  # Oldest messages first
    return [{"role": role, "content": content} for role, content in rows]

# Allowed channels functions
def add_allowed_channel(channel_id, channel_name):
    """Add a channel to the allowed_channels table."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO allowed_channels (channel_id, channel_name) VALUES (?, ?)",
        (channel_id, channel_name)
    )
    conn.commit()
    conn.close()

def get_allowed_channels():
    """Return a set of allowed channel IDs."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM allowed_channels")
    rows = cursor.fetchall()
    conn.close()
    return {row[0] for row in rows}

# --- Message Detection ---

def is_message_directed_at_bot(message):
    """Determine if Lum should respond to a message."""
    if isinstance(message.channel, discord.DMChannel):
        return True
    if client.user in message.mentions:
        return True
    content = message.content.lower().strip()
    greeting_patterns = [r"^(hello|hi|hey|yo|sup) lum\b", r"^(hello|hi|hey|yo|sup)\s*$"]
    if any(re.match(pattern, content) for pattern in greeting_patterns):
        return True
    if re.match(r"^(hello|hi|hey|yo|sup)\s+\S+", content):
        return False
    trigger_keywords = ["lum", "bot", "question", "help", "how are you", "what's", "whats", "who", "define", "explain"]
    if any(content.startswith(word) for word in trigger_keywords):
        return True
    if content.endswith("?"):
        return True
    return False

# --- Event Handlers ---

@client.event
async def on_ready():
    print(f"✅ logged in as {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    lower_content = message.content.lower()

    # Always allow !setchannel command (even if not in an allowed channel)
    if lower_content.startswith("!setchannel"):
        await handle_set_channel(message)
        return

    # Allow setting language regardless of allowed channel:
    if lower_content.startswith("lum set language to"):
        await handle_set_language(message)
        return

    # For guild messages, only process if the channel is in allowed_channels.
    if message.guild is not None:
        allowed_channels = get_allowed_channels()
        if message.channel.id not in allowed_channels:
            return

    # Explicit command handling for !gif or !img
    if lower_content.startswith("!gif"):
        await handle_gif_search(message)
        return
    if lower_content.startswith("!img"):
        await handle_image_generation(message)
        return

    # Process natural language commands if the message is directed at Lum.
    if is_message_directed_at_bot(message):
        # Opinion-related commands
        if lower_content.startswith("lum set your opinion on"):
            await handle_set_opinion(message)
            return
        if lower_content.startswith("lum what") and "your opinion on" in lower_content:
            await handle_get_opinion(message)
            return

        # Natural language image-generation requests.
        generate_match = re.search(r'\blum\s+generate\s+(.+)', lower_content)
        if generate_match:
            prompt = generate_match.group(1).strip()
            if prompt:
                await handle_image_generation(message, prompt_override=prompt)
                return

        # Natural language GIF requests (e.g., "with a gif" or "as a gif")
        if "with a gif" in lower_content or "as a gif" in lower_content:
            if "with a gif" in lower_content:
                parts = lower_content.split("with a gif", 1)
            else:
                parts = lower_content.split("as a gif", 1)
            query = parts[0].strip()
            if query.startswith("lum"):
                query = query[3:].strip()
            if query:
                await handle_gif_search(message, query_override=query)
            else:
                await message.reply("please provide a search term for the gif.")
            return

        # Natural language random GIF requests.
        if "give me" in lower_content and "random gif" in lower_content:
            await handle_random_gif(message)
            return

        # Otherwise, handle as a normal text interaction.
        await handle_bot_message(message)

# --- Command Handlers ---

async def handle_set_channel(message):
    """Register the channel so that Lum will speak here."""
    channel_id = message.channel.id
    channel_name = message.channel.name if hasattr(message.channel, "name") else "DM"
    add_allowed_channel(channel_id, channel_name)
    await message.reply(f"Channel **{channel_name}** has been set for Lum to speak.")

async def handle_set_language(message):
    """
    Set Lum's language.
    Expected format: "lum set language to <language>"
    """
    # Extract the language by removing the command prefix.
    language = message.content[len("lum set language to"):].strip()
    if not language:
        await message.reply("please provide a language, e.g. 'lum set language to french'")
        return
    set_lum_preference("language", language)
    await message.reply(f"Language set to **{language}**.")

async def handle_set_opinion(message):
    """
    Set Lum's opinion on a subject.
    Expected format: "lum set your opinion on <subject> to <opinion>"
    """
    pattern = r"lum set your opinion on\s+(.+?)\s+(?:to|as)\s+(.+)"
    match = re.search(pattern, message.content, re.IGNORECASE)
    if not match:
        await message.reply("Please use the format: 'lum set your opinion on <subject> to <opinion>'.")
        return
    subject = match.group(1).strip()
    opinion = match.group(2).strip()
    set_lum_preference(subject, opinion)
    await message.reply(f"Okay, I've set my opinion on **{subject}** to: {opinion}")

async def handle_get_opinion(message):
    """
    Retrieve Lum's opinion on a subject.
    Expected format: "lum what's/whats/what is your opinion on <subject>"
    If no opinion exists, generate one using ChatGPT and save it.
    """
    pattern = r"lum what(?:'s|s| is) your opinion on\s+(.+)"
    match = re.search(pattern, message.content, re.IGNORECASE)
    if not match:
        await message.reply("Please ask in the format: 'lum what's your opinion on <subject>'.")
        return
    subject = match.group(1).strip()
    opinion = get_lum_preference(subject)
    if opinion:
        await message.reply(f"My opinion on **{subject}** is: {opinion}")
    else:
        # Generate an opinion using ChatGPT.
        language = get_lum_preference("language")
        language_instruction = f" Respond in {language}." if language else ""
        prompt = (f"You are lum, a laid-back discord bot with strong opinions about everything."
                  f" Provide your opinion on {subject} in one short sentence.{language_instruction}"
                  " Do not say that you have no opinion.")
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are lum, a laid-back discord bot with strong opinions and a casual tone."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=60
            )
            generated_opinion = response["choices"][0]["message"]["content"].strip()
            set_lum_preference(subject, generated_opinion)
            await message.reply(f"My opinion on **{subject}** is: {generated_opinion}")
        except Exception as e:
            await message.reply("Sorry, I couldn't generate an opinion on that right now.")
            print(f"❌ Error generating opinion: {e}")

async def handle_bot_message(message):
    """Handle regular text interactions using conversation history and ChatGPT."""
    await message.channel.typing()
    user_id = message.author.id
    content = message.content.lower().strip()
    
    # Save the user's message permanently.
    add_conversation_message(user_id, "user", content)
    
    # Retrieve the last 10 messages for context.
    history = get_conversation_history(user_id, limit=10)
    user_memory = get_user_memory(user_id, "user_name")
    memory_intro = f"this user is called {user_memory}. " if user_memory else ""
    language = get_lum_preference("language")
    language_instruction = f" Respond in {language}." if language else ""
    
    try:
        chat_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": 
                 f"you are lum, a laid-back discord bot. "
                 f"you always use lowercase and keep responses short. {memory_intro}"
                 "never add extra words, never start a conversation, never introduce yourself. "
                 "if someone greets you, respond minimally ('hey.', 'yo.', 'hi.') and nothing more. "
                 "if someone asks how you're doing, respond with 'fine.' or 'alright.'. "
                 "if answering a factual question, provide the answer and stop. do not add anything extra."
                 f"{language_instruction}"
                }
            ] + history,
            max_tokens=200
        )
        bot_reply = chat_response["choices"][0]["message"]["content"].lower()
        
        # Save the bot's reply permanently.
        add_conversation_message(user_id, "assistant", bot_reply)
        await message.reply(bot_reply)
    except Exception as e:
        print(f"❌ OpenAI response error: {e}")

async def handle_image_generation(message, prompt_override=None):
    """Generate an image using OpenAI's image API."""
    prompt = prompt_override if prompt_override is not None else message.content[len("!img"):].strip()
    if not prompt:
        await message.reply("please provide an image prompt.")
        return
    await message.channel.typing()
    try:
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        image_url = response["data"][0]["url"]
        await message.reply(image_url)
    except Exception as e:
        await message.reply("sorry, there was an error generating the image.")
        print(f"❌ Image generation error: {e}")

async def handle_gif_search(message, query_override=None):
    """
    Handle GIF search using the Giphy API.
    If query_override is provided, use that as the search term;
    otherwise, extract the query from the message (after '!gif').
    """
    query = query_override if query_override is not None else message.content[len("!gif"):].strip()
    if not query:
        await message.reply("please provide a search term for the gif.")
        return
    await message.channel.typing()
    if not GIPHY_API_KEY:
        await message.reply("Giphy API key is not set.")
        return

    params = {
        "api_key": GIPHY_API_KEY,
        "q": query,
        "limit": 1,
        "offset": 0,
        "rating": "g",
        "lang": "en"
    }
    try:
        response = requests.get("https://api.giphy.com/v1/gifs/search", params=params)
        data = response.json()
        if data["data"]:
            gif_url = data["data"][0]["images"]["original"]["url"]
            await message.reply(gif_url)
        else:
            # Fall back to a random gif if no search results.
            await handle_random_gif(message)
    except Exception as e:
        await message.reply("sorry, there was an error searching for a gif.")
        print(f"❌ Giphy API error: {e}")

async def handle_random_gif(message):
    """Fetch and send a random gif using Giphy's random endpoint."""
    await message.channel.typing()
    if not GIPHY_API_KEY:
        await message.reply("Giphy API key is not set.")
        return

    params = {"api_key": GIPHY_API_KEY, "rating": "g"}
    try:
        response = requests.get("https://api.giphy.com/v1/gifs/random", params=params)
        data = response.json()
        if data.get("data") and data["data"].get("images") and data["data"]["images"].get("original"):
            gif_url = data["data"]["images"]["original"]["url"]
            await message.reply(gif_url)
        else:
            await message.reply("sorry, I couldn't find a random gif.")
    except Exception as e:
        await message.reply("sorry, there was an error fetching a random gif.")
        print(f"❌ Giphy API error: {e}")

client.run(DISCORD_TOKEN)
