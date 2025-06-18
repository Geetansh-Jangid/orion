# === MODULES ===
from google.genai import types
from google import genai
import os
import discord
from discord import app_commands
from dotenv import load_dotenv
import asyncio
import io
from threading import Thread
from flask import Flask
from dotenv import load_dotenv
import threading

PORT = int(os.getenv("PORT", 8080))  
app = Flask(__name__)
@app.route('/')
def home():
    return "Booted Jarvis!"

@app.route('/health')
def health():
    return "OK", 200
# --- Load Environment Variables ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ===============================================================
# ===== CORE GEMINI AND BOT SETUP ===============================
# ===============================================================

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID="gemini-2.5-flash-preview-05-20"

SYSTEM_INSTRUCTION = """you are a friendly chatbot which always answer in a very consise way answering only what is asked
you must search the web to answer each and every question
you must print the list of sources which you used to answer the query.
always use latex and markdown to make you answers stand out
draw graphs(always execute matplotlib code to draw graphs) and tables to simplify things
you are orion bot created by Geetansh Jangid, you are not created by google."""

GENERATE_CONTENT_CONFIG = types.GenerateContentConfig(
    tools=[
        types.Tool(code_execution=types.ToolCodeExecution()),
        types.Tool(google_search=types.GoogleSearch()),
    ],
    system_instruction=[
        types.Part.from_text(text=SYSTEM_INSTRUCTION),
    ],
)

# --- BOT STATE MANAGEMENT ---
conversation_history = {}
active_channels = set()

# --- DISCORD CLIENT AND COMMAND TREE SETUP ---
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)
tree = app_commands.CommandTree(discord_client)

# ===============================================================
# ===== ASYNC GENERATOR =========================================
# ===============================================================

async def get_gemini_response_stream(api_contents, files_to_send: list):
    """
    Yields text chunks from the Gemini API and appends any generated files
    to the 'files_to_send' list provided as an argument.
    """
    response_stream = client.models.generate_content_stream(
        model=MODEL_ID,
        contents=api_contents,
        config=GENERATE_CONTENT_CONFIG,
    )

    for chunk in response_stream:
        if chunk.candidates and chunk.candidates[0].content.parts:
            for part in chunk.candidates[0].content.parts:
                if part.text:
                    yield part.text

                if hasattr(part, 'inline_data') and part.inline_data and hasattr(part.inline_data, 'data') and part.inline_data.data:
                    image_data = part.inline_data.data
                    file = discord.File(io.BytesIO(image_data), filename="output.png")
                    files_to_send.append(file)

# ===============================================================
# ===== SLASH COMMANDS ==========================================
# ===============================================================

def _create_help_embed():
    """Helper function to create the help embed."""
    embed = discord.Embed(
        title="Orion Bot Help",
        description="I am a friendly, concise chatbot created by Geetansh Jangid.",
        color=discord.Color.teal()
    )
    embed.add_field(
        name="💬 Conversational Chat",
        value=f"Mention me with your question!\n**Example:** `@{discord_client.user.name} what is a black hole?`",
        inline=False
    )
    embed.add_field(
        name="🚀 Slash Commands",
        value="`/search [prompt]` - Get a direct answer to a single question (no memory).\n`/help` - Shows this help message.",
        inline=False
    )
    embed.add_field(
        name="👂 Activation Commands",
        value="`?activate` - I will listen to all messages in this channel without being tagged.\n`?deactivate` - I will stop listening to all messages.\n`?clear` - Clears my conversation memory in this channel.",
        inline=False
    )
    embed.set_footer(text="Orion, made by Geetansh Jangid")
    return embed

@tree.command(name="help", description="Shows the bot's help and command information.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_create_help_embed(), ephemeral=True)

@tree.command(name="search", description="Search for information without using conversation history.")
@app_commands.describe(prompt="The question you want to ask.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.user_install()
async def search_command(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer()
    files = []
    full_text_response = ""
    api_contents = [types.Content(role='user', parts=[types.Part(text=prompt)])]

    try:
        response_generator = get_gemini_response_stream(api_contents, files)
        async for text_chunk in response_generator:
            full_text_response += text_chunk
            if len(full_text_response) % 100 == 0 or len(full_text_response) < 100:
                if full_text_response:
                    await interaction.edit_original_response(content=full_text_response)

        if full_text_response:
            await interaction.edit_original_response(content=full_text_response)
        else:
            await interaction.edit_original_response(content="I've processed the request, but there's no text output to display.")

        if files:
            await interaction.followup.send(files=files)

    except Exception as e:
        print(f"An error occurred in /search: {e}")
        await interaction.edit_original_response(content="I'm sorry, an error occurred while processing your request.")

# ===============================================================
# ===== DISCORD EVENTS ==========================================
# ===============================================================

@discord_client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {discord_client.user}. Slash commands synced. Bot is ready.")
    activity = discord.Activity(name="for @mentions and /help", type=discord.ActivityType.listening)
    await discord_client.change_presence(activity=activity)

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user:
        return

    channel_id = message.channel.id
    content = message.content.strip()

    if content.lower() == '?activate':
        active_channels.add(channel_id)
        await message.channel.send(f"✅ Orion Bot is now active in this channel. I will respond to all messages. Use `?deactivate` to turn this off.")
        return

    if content.lower() == '?deactivate':
        active_channels.discard(channel_id)
        await message.channel.send(f"⛔ Orion Bot is no longer active. Mention me (`@{discord_client.user.name}`) to chat.")
        return

    if content.lower() == '?clear':
        if channel_id in conversation_history:
            del conversation_history[channel_id]
            await message.channel.send("🧹 My memory of our conversation in this channel has been cleared.")
        else:
            await message.channel.send("There's no conversation history to clear in this channel!")
        return

    is_mentioned = discord_client.user.mentioned_in(message)
    is_active = channel_id in active_channels

    if not is_mentioned and not is_active:
        return

    prompt = content.replace(f'<@!{discord_client.user.id}>', '').strip()
    if not prompt:
        return

    if channel_id not in conversation_history:
        conversation_history[channel_id] = []

    # --- HISTORY FIX 1/3: Save the user prompt with the simplified format ---
    conversation_history[channel_id].append({'role': 'user', 'parts': prompt})

    try:
        # --- HISTORY FIX 2/3: Build the API request from the simplified format ---
        # Note the change: No inner loop. `item['parts']` is now a string.
        api_contents = [
            types.Content(role=item['role'], parts=[types.Part(text=item['parts'])])
            for item in conversation_history[channel_id]
        ]

        response_message = await message.channel.send("Thinking... 🤔")
        full_text_response = ""
        files_to_send = []

        response_generator = get_gemini_response_stream(api_contents, files_to_send)
        async for text_chunk in response_generator:
            full_text_response += text_chunk
            if len(full_text_response) % 150 == 0 and full_text_response:
                await response_message.edit(content=full_text_response)

        if full_text_response:
            await response_message.edit(content=full_text_response)
            # --- HISTORY FIX 3/3: Save the model response with the simplified format ---
            conversation_history[channel_id].append({'role': 'model', 'parts': full_text_response})
        else:
            await response_message.edit(content="I've processed the request, but there's no text output to display.")
            # Pop the user's prompt if the model had no response, to keep history clean
            conversation_history[channel_id].pop()

        if files_to_send:
            await message.channel.send(files=files_to_send)

    except Exception as e:
        print(f"An error occurred in on_message: {e}")
        if 'response_message' in locals():
            await response_message.edit(content="I'm sorry, an error occurred while processing your request.")
        else:
            await message.channel.send("I'm sorry, an error occurred while processing your request.")

        # Pop the user's prompt on error to prevent a broken history state
        if channel_id in conversation_history and conversation_history[channel_id]:
            conversation_history[channel_id].pop()

# --- Run the Bot ---
def run_flask():
    app.run(debug=False, host='0.0.0.0', port=PORT)

flask_thread = threading.Thread(target=run_flask)
flask_thread.daemon = True  # Allow the main thread to exit even if the Flask thread is running
flask_thread.start()
discord_client.run(DISCORD_TOKEN)
