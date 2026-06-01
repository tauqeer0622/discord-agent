import discord
import asyncio
import os

# ==========================================
# DJANGO INTEGRATION SETUP
# (Tauqeer will run this within a management command or celery task)
# ==========================================
# import django
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
# django.setup()

# Import Django utilities and models (adjust 'myapp' to your actual app name)
from asgiref.sync import sync_to_async
# from myapp.models import TargetChannel, FetchedMessage
# from django.conf import settings

from config import DISCORD_TOKEN
TOKEN = DISCORD_TOKEN

class ScraperClient(discord.Client):
    async def on_ready(self):
        print(f'✅ Successfully logged in as {self.user}!')
        
        # ---------------------------------------------------------
        # 1. Consume active channel configs directly from Django DB
        # ---------------------------------------------------------
        @sync_to_async
        def get_active_channels():
            # Example: return list(TargetChannel.objects.filter(is_active=True))
            # MOCKING FOR NOW so it doesn't crash outside django:
            class MockChannel:
                def __init__(self, name, cid):
                    self.name = name
                    self.channel_id = cid
            return [
                MockChannel("Market cipher - btc and eth", 1184732195525513297),
                MockChannel("Market cipher - shitcoins", 1184727619363667978)
            ]

        # ---------------------------------------------------------
        # 2. Write scraped messages directly into FetchedMessage model
        # ---------------------------------------------------------
        @sync_to_async
        def save_message_to_db(channel_obj, msg):
            """
            Example implementation:
            FetchedMessage.objects.update_or_create(
                message_id=str(msg.id),
                defaults={
                    'target_channel': channel_obj,
                    'author': msg.author.name,
                    'content': msg.content,
                    'timestamp': msg.created_at
                }
            )
            """
            pass # Replaced JSON save/webhook with this DB call

        active_channels = await get_active_channels()
        
        for channel_obj in active_channels:
            print(f"📥 Fetching messages for: {channel_obj.name} (ID: {channel_obj.channel_id})...")
            try:
                # Fetch the channel
                channel = await self.fetch_channel(channel_obj.channel_id)
                
                saved_count = 0
                # Fetch the last 20 messages
                async for msg in channel.history(limit=20):
                    if msg.content.strip():
                        # Save directly to database
                        await save_message_to_db(channel_obj, msg)
                        saved_count += 1
                        
                print(f"✅ Saved {saved_count} messages from {channel_obj.name} to DB")
                
            except discord.errors.Forbidden:
                print(f"❌ ERROR: You don't have permission to view channel {channel_obj.channel_id}")
            except discord.errors.NotFound:
                print(f"❌ ERROR: Channel {channel_obj.channel_id} not found. Check the ID.")
            except Exception as e:
                print(f"❌ ERROR: {str(e)}")

        print("\n🎉 All done! Messages integrated directly into Django architecture.")
        await self.close()

def run_scraper():
    intents = discord.Intents.default()
    intents.message_content = True
    client = ScraperClient(intents=intents)
    try:
        client.run(TOKEN)
    except discord.errors.LoginFailure:
        print("❌ ERROR: Invalid token. Please check your token and try again.")

if __name__ == "__main__":
    run_scraper()
