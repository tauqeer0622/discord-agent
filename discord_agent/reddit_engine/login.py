import os
import sys
import time

# Ensure package path is resolved
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reddit_engine.browser_controller import controller
from reddit_engine.database import add_log


def main():
    print("=" * 60)
    print("  Reddit Interactive Login Helper")
    print("=" * 60)
    print()
    print("[*] Launching visual Google Chrome window...")
    print("[*] Please log into your Reddit account in the opened browser.")
    print("[*] Your session cookies and login tokens will be permanently")
    print("    saved in ./browser_data for hands-free autonomous posting.")
    print()

    # Always launch visual for interactive human login
    if not controller.start(headless=False):
        print("❌ Failed to start browser. Please check Chrome/Chromium installation.")
        return

    try:
        controller.page.goto("https://www.reddit.com/login", timeout=35000)
    except Exception as e:
        print(f"Notice during navigation: {e}")

    print("[*] Waiting for you to complete login in the browser window...")
    print("    (You have 5 minutes. Feel free to complete 2FA or passkeys).")
    print()

    start_time = time.time()
    logged_in = False

    while time.time() - start_time < 300:
        time.sleep(3)
        if controller.is_logged_in():
            logged_in = True
            break

    if logged_in:
        print("=" * 60)
        print("  ✅ SUCCESS: Successfully logged into Reddit!")
        print("  Session preserved in ./reddit_engine/browser_data")
        print("=" * 60)
        add_log("SUCCESS", "Interactive Reddit login completed successfully.")
    else:
        print("⚠️ Login timeout or not detected. Please try again.")

    time.sleep(2)
    controller.stop()


if __name__ == "__main__":
    main()
