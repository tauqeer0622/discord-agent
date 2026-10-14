import os
import sys
import argparse

# Resolve package paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reddit_engine.config import config
from reddit_engine.browser_controller import controller
from reddit_engine.engine import engine


def main():
    parser = argparse.ArgumentParser(description="Test Reddit Card Post")
    parser.add_argument("--sub", type=str, default="test", help="Target subreddit (default: 'test')")
    parser.add_argument("--dry-run", action="store_true", help="Simulate post without submitting")
    parser.add_argument("--image", type=str, default=None, help="Custom image path (optional)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Reddit Engine Post Test")
    print("=" * 60)
    print(f"Target Subreddit: r/{args.sub}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE SUBMISSION'}")

    img_path = args.image or engine._get_latest_market_card()
    if not img_path:
        print("❌ No market card found to test. Please ensure x_engine/media has generated images.")
        return

    print(f"Image Card: {img_path}")
    title = engine._format_post_title()
    print(f"Title: {title}")
    print()

    res = controller.post_image_to_subreddit(
        subreddit=args.sub,
        title=title,
        image_path=img_path,
        first_comment=config.first_comment_cta,
        dry_run=args.dry_run
    )

    print()
    print("=" * 60)
    print(f"Result: {res}")
    print("=" * 60)
    controller.stop()


if __name__ == "__main__":
    main()
