import asyncio
import json
import os
import re
import aiohttp
from playwright.async_api import async_playwright

# ==========================================
# CONFIGURE WEBHOOK ENDPOINT
# ==========================================
WEBHOOK_URL = "http://127.0.0.1:8000/webhook/messages/"

async def scrape_channel(page, server_name, channel_name):
    print(f"\n--- Navigating to Server: {server_name} ---")
    try:
        # Discord servers on the left sidebar are usually treeitems with aria-labels matching the server name
        server_icon = page.get_by_role("treeitem", name=re.compile(server_name, re.IGNORECASE)).first
        await server_icon.click(timeout=10000)
        await page.wait_for_timeout(2000) # Wait for channels to load
        
        print(f"Navigating to Channel: {channel_name}")
        # Discord channels are usually links in the sidebar
        channel_link = page.get_by_role("link", name=re.compile(channel_name, re.IGNORECASE)).first
        await channel_link.click(timeout=10000)
        await page.wait_for_timeout(3000) # Wait for messages to load
        
        print(f"Scraping recent messages...")
        messages = []
        
        # Discord messages have ids like chat-messages-123456789
        message_elements = await page.locator('li[id^="chat-messages-"]').all()
        
        # Get the last 15 messages
        for el in message_elements[-15:]:
            text = await el.inner_text()
            
            # Extract any hyperlinks embedded in the message (like thread links)
            links = await el.evaluate("""(node) => {
                return Array.from(node.querySelectorAll('a')).map(a => a.href);
            }""")
            
            # Filter out empty or duplicate links
            unique_links = []
            for link in links:
                if link and link.startswith("http") and link not in unique_links:
                    unique_links.append(link)
            
            # Clean up the text a bit (removing newlines, etc for cleaner JSON)
            clean_text = "\n".join([line for line in text.split("\n") if line.strip()])
            
            if unique_links:
                clean_text += "\n\nExtracted Links:\n" + "\n".join(unique_links)
                
            messages.append(clean_text)
            
        print(f"Successfully scraped {len(messages)} messages from {channel_name}.")
        return messages
    except Exception as e:
        print(f"Error scraping {server_name} -> {channel_name}: {str(e)}")
        # Save a screenshot for debugging if it fails
        os.makedirs("debug", exist_ok=True)
        await page.screenshot(path=f"debug/error_{server_name}_{channel_name}.png")
        return []

async def run():
    # Setup directory for video recording
    os.makedirs("videos", exist_ok=True)
    
    async with async_playwright() as p:
        print("Launching browser... (Using persistent profile to save your login)")
        
        # Use a persistent context so the user doesn't have to log in every single time!
        context = await p.chromium.launch_persistent_context(
            user_data_dir="discord_profile",
            headless=False,
            record_video_dir="videos/",
            record_video_size={"width": 1280, "height": 720}
        )
        page = context.pages[0] if context.pages else await context.new_page()

        print("Navigating to Discord...")
        await page.goto("https://discord.com/app")

        print("\n" + "="*50)
        print("ACTION REQUIRED: PLEASE LOG IN MANUALLY")
        print("="*50)
        print("An automated browser window has just opened.")
        print("Please use that window to log into your 'Bright Axis Account'.")
        print("You have 3 minutes to enter your email, password, and solve any CAPTCHAs.")
        print("Waiting for you to log in...\n")
        
        try:
            # Wait until we are logged in and redirected to the channels page
            await page.wait_for_url("**/channels/**", timeout=180000)
            print("Successfully logged in!")
            
            # Discord often shows popups (like Nitro ads). Press Escape to clear them.
            await page.wait_for_timeout(3000)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
            await page.keyboard.press("Escape")
            
        except Exception as e:
            print("Timeout waiting for login. You didn't log in fast enough, or closed the window.")
            await context.close()
            await browser.close()
            return

        # Give discord a moment to fully load
        await page.wait_for_timeout(5000)

        results = {}

        # Dynamically fetch channel configs from Django Dashboard
        print("Fetching channel configurations from Django dashboard...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8000/api/configs/") as response:
                    if response.status == 200:
                        configs = await response.json()
                        print(f"Found {len(configs)} active channel configs.")
                    else:
                        print(f"Failed to fetch configs. HTTP Status: {response.status}")
                        configs = []
        except Exception as e:
            print(f"Error connecting to Django dashboard: {e}")
            configs = []

        for config in configs:
            label = config.get("label", "")
            if " - " in label:
                server_name, channel_name = label.split(" - ", 1)
                results[label] = await scrape_channel(page, server_name.strip(), channel_name.strip())
            else:
                print(f"Invalid label format (expected 'Server - Channel'): {label}")

        # Save results to JSON
        with open("fetched_messages.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
            
        print("\n--- Scraping Complete ---")
        print("Results saved to fetched_messages.json")
        print("A video recording of this session has been saved in the 'videos/' directory.")
        
        # Push to webhook if configured
        if WEBHOOK_URL:
            print(f"🚀 Pushing payload to Django webhook: {WEBHOOK_URL}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(WEBHOOK_URL, json=results) as response:
                        if response.status in (200, 201):
                            print("✅ Successfully pushed to Django backend!")
                        else:
                            print(f"⚠️ Webhook responded with status: {response.status}")
                            print(await response.text())
            except Exception as e:
                print(f"❌ ERROR: Failed to push to webhook: {str(e)}")

        # Close everything
        await context.close()

if __name__ == "__main__":
    asyncio.run(run())
