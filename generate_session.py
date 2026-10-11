# Run this locally on your own computer, not through a Telegram bot.
# It prompts in the terminal for your phone, Telegram login code, and (if enabled) 2FA password.
import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print("\nYour SESSION_STRING (keep it secret):\n")
        print(client.session.save())
        print("\nAdd it to Render as the SESSION_STRING environment variable.")
        print("Do not send it to anyone or commit it to a public repository.")

asyncio.run(main())
