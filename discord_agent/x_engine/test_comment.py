import sys
import argparse
from browser_controller import browser_controller
from comment_generator import generator
from config import config
from database import init_db, save_comment, save_post

def main():
    parser = argparse.ArgumentParser(description="Single Tweet Comment Tester")
    parser.add_argument("--url", help="Direct URL of the tweet to comment on")
    parser.add_argument("--author", default="target_user", help="Author handle of the tweet")
    parser.add_argument("--topic", default="crypto", choices=["crypto", "stocks", "BTC"], help="Topic keyword")
    parser.add_argument("--live", action="store_true", help="Post live instead of dry-run")
    args = parser.parse_args()

    init_db()

    print("=" * 60)
    print("   APEXPULSE • SINGLE TWEET COMMENT TESTER")
    print("=" * 60)

    url = args.url
    if not url:
        url = input("Enter the X/Twitter post URL to test on: ").strip()

    if not url:
        print("[!] No URL provided. Exiting.")
        sys.exit(1)

    # Extract tweet id from URL
    tweet_id = url.split("/status/")[1].split("?")[0].split("/")[0] if "/status/" in url else "test_id"

    sample_text = input("Enter the tweet's text or topic (press Enter for sample text): ").strip()
    if not sample_text:
        sample_text = f"Discussing the latest market movements in {args.topic} and price predictions."

    print(f"\n[*] Target Post: {url}")
    print(f"[*] Author: @{args.author}")
    print(f"[*] Topic: {args.topic}")

    # Generate reply
    print("\n[*] Generating contextual comment...")
    reply = generator.generate(sample_text, args.author, args.topic)
    
    print("\n" + "-" * 50)
    print(" GENERATED COMMENT:")
    print(f" \"{reply}\"")
    print(f" Length: {len(reply)} / 280 characters")
    print("-" * 50 + "\n")

    is_dry_run = not args.live
    if is_dry_run:
        choice = input("Would you like to post this LIVE to X? (y/N): ").strip().lower()
        if choice == "y":
            is_dry_run = False

    mode_str = "LIVE POST" if not is_dry_run else "DRY RUN (Simulation)"
    print(f"[*] Executing in {mode_str} mode...")

    # Start browser visible so user can see it happen
    browser_controller.start(headless=False)
    
    from media_manager import get_media_image_to_post
    image_path = get_media_image_to_post()
    if image_path:
        print(f"[*] Attaching Daily Image: {image_path}")

    result = browser_controller.post_reply(
        tweet_id=tweet_id,
        tweet_url=url,
        reply_text=reply,
        image_path=image_path,
        dry_run=is_dry_run
    )

    print("\n" + "=" * 60)
    if result["success"]:
        print(f"[SUCCESS] {result.get('message', 'Action completed!')}")
        save_comment(tweet_id, args.author, reply, "live" if not is_dry_run else "dry_run", "success")
    else:
        print(f"[FAILED] Error: {result.get('message', 'Unknown error')}")
        save_comment(tweet_id, args.author, reply, "live" if not is_dry_run else "dry_run", "failed", result.get("message"))
    print("=" * 60)

    input("\nPress [ENTER] to close the browser... ")
    browser_controller.stop()

if __name__ == "__main__":
    main()
