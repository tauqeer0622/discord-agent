import argparse
import sys
import uvicorn
from config import config
from database import init_db, add_log

BANNER = """
====================================================================
    ___    ____  _______  __ ____  __  ____   _____ ______
   /   |  / __ \/ ____/ |/ // __ \/ / / / /  / ___// ____/
  / /| | / /_/ / __/  |   // /_/ / / / / /   \__ \/ __/   
 / ___ |/ ____/ /___ /   |/ ____/ /_/ / /______/ / /___   
/_/  |_/_/   /_____//_/|_/_/    \____/_____/____/_____/   
   Autonomous X Commenting & Growth Engine for Crypto & Stocks
====================================================================
"""

def main():
    parser = argparse.ArgumentParser(description="Autonomous X Commenting Engine")
    parser.add_argument("--cli", action="store_true", help="Run in CLI headless mode without web dashboard")
    parser.add_argument("--host", default=config.web_host, help="Web dashboard host")
    parser.add_argument("--port", type=int, default=config.web_port, help="Web dashboard port")
    parser.add_argument("--live", action="store_true", help="Enable live commenting mode (overrides DRY_RUN)")
    args = parser.parse_args()

    print(BANNER)
    init_db()

    if args.live:
        config.dry_run = False
        print("[!] LIVE MODE ENABLED: Comments will be published directly to X.")
    else:
        print("[i] DRY RUN MODE ACTIVE: Safe testing mode without publishing.")

    if args.cli:
        print("[*] Starting autonomous engine in CLI mode...")
        from engine import engine
        engine.start()
        try:
            while True:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] Stopping engine...")
            engine.stop()
            sys.exit(0)
    else:
        print(f"[*] Starting Web Dashboard on http://{args.host}:{args.port}")
        add_log("INFO", f"Dashboard server starting on http://{args.host}:{args.port}")
        uvicorn.run("server:app", host=args.host, port=args.port, reload=False, log_level="info")

if __name__ == "__main__":
    main()
