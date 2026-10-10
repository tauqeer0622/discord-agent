import os
import sys
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from .browser_controller import browser_controller
    from .database import add_log
except (ImportError, ValueError):
    from browser_controller import browser_controller
    from database import add_log

def main():
    print("=" * 60)
    print("   X ACCOUNT LOGIN HELPER")
    print("=" * 60)
    print("[*] Launching visible Chrome browser...")
    
    success = browser_controller.start(headless=False)
    if not success or not browser_controller.page:
        print("[!] Failed to launch browser. Please ensure Playwright Chromium is installed.")
        sys.exit(1)

    print("[*] Navigating to https://x.com/login ...")
    browser_controller.page.goto("https://x.com/login")
    
    print("\n" + "=" * 60)
    print(" ACTION REQUIRED:")
    print(" 1. In the browser window that just opened, log into your new X account.")
    print(" 2. Complete any 2FA or verification steps if prompted.")
    print(" 3. Once you see your X home timeline / feed, come back here.")
    print("=" * 60 + "\n")

    try:
        input(">>> Press [ENTER] in this terminal when you have completed login... ")
    except KeyboardInterrupt:
        print("\n[*] Aborted by user.")
    
    # Verify login & account health
    print("[*] Checking session and account health...")
    status_info = browser_controller.check_account_status()
    cookies = browser_controller.context.cookies() if browser_controller.context else []
    auth_cookie = next((c["value"] for c in cookies if c["name"] == "auth_token"), None)

    if auth_cookie:
        # Update .env in both current dir and parent root
        candidate_envs = [
            os.path.join(current_dir, ".env"),
            os.path.join(current_dir, "..", ".env"),
            os.path.abspath(".env")
        ]
        for env_path in candidate_envs:
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                found = False
                for line in lines:
                    if line.startswith("X_AUTH_TOKEN="):
                        new_lines.append(f"X_AUTH_TOKEN={auth_cookie}\n")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"\nX_AUTH_TOKEN={auth_cookie}\n")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                print(f"[+] Saved fresh X_AUTH_TOKEN to {env_path} automatically.")

    if status_info.get("status") == "suspended":
        print("\n[!] WARNING: This X account is SUSPENDED or in READ-ONLY mode by X!")
        print("    " + status_info.get("message", ""))
        print("    You must use an active, non-suspended X account or submit an appeal on X.")
        add_log("WARN", f"Manual login completed, but account is suspended: {status_info.get('message')}")
    elif status_info.get("logged_in"):
        print("\n[SUCCESS] Login verified and account is healthy!")
        print("[SUCCESS] Your session is permanently saved to ./browser_data.")
        add_log("SUCCESS", "Manual login verified and account is active.")
    else:
        print("\n[NOTE] Could not confirm login. Please verify you completed login in the browser.")

    browser_controller.stop()
    print("[*] Browser closed. You are ready to run the automation!")

if __name__ == "__main__":
    import os
    main()
