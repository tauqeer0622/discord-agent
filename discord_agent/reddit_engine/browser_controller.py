import os
import time
import random
import threading
from typing import Dict, Any, Optional
from playwright.sync_api import sync_playwright

from .config import config
from .database import add_log, record_post


class RedditBrowserController:
    """
    Playwright-powered stealth browser controller for autonomous Reddit posting.
    Supports persistent user sessions, direct image uploads, and follow-up comments.
    """

    def __init__(self):
        self._playwright = None
        self.context = None
        self.page = None
        self.is_running = False
        self._thread_id = None

    def is_alive(self) -> bool:
        """Returns True if the browser context and page are active and responsive."""
        if not self.is_running or not self.context or not self.page:
            return False
        if self._thread_id != threading.get_ident():
            return False
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    def ensure_active(self, headless: Optional[bool] = None) -> bool:
        """Ensures an active browser context exists, restarting if closed."""
        if not self.is_alive():
            self.stop()
            return self.start(headless=headless)
        return True

    def start(self, headless: Optional[bool] = None) -> bool:
        """Launches Playwright with a persistent user data directory to preserve Reddit login state."""
        if self.is_alive():
            return True

        self.stop()

        use_headless = True if os.name != "nt" else (config.headless if headless is None else headless)
        data_dir = config.browser_data_dir
        if not os.path.isabs(data_dir):
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), data_dir)
        data_dir = os.path.abspath(data_dir)
        os.makedirs(data_dir, exist_ok=True)

        try:
            self._thread_id = threading.get_ident()
            self._playwright = sync_playwright().start()

            # Anti-detection stealth flags
            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--mute-audio",
                "--no-first-run",
                "--disable-features=Translate,OptimizationHints,MediaRouter",
            ]
            if os.name != "nt":
                args.extend([
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ])

            launch_kwargs = {
                "user_data_dir": data_dir,
                "headless": use_headless,
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "viewport": {"width": 1280, "height": 850},
                "args": args,
            }

            def _try_launch():
                if os.name == "nt":
                    try:
                        return self._playwright.chromium.launch_persistent_context(
                            channel="chrome",
                            **launch_kwargs
                        )
                    except Exception:
                        pass
                return self._playwright.chromium.launch_persistent_context(**launch_kwargs)

            self.context = _try_launch()
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

            # Anti-detection stealth script injection
            stealth_js = """
            try {
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                delete Object.getPrototypeOf(navigator).webdriver;
            } catch(e) {}
            window.chrome = { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
            """
            self.page.add_init_script(stealth_js)
            self.is_running = True
            add_log("INFO", f"Reddit browser controller started (headless={use_headless})")
            return True
        except Exception as e:
            add_log("ERROR", f"Failed to start Reddit browser: {str(e)}")
            self.stop()
            return False

    def stop(self):
        """Cleanly halts the browser session and frees memory."""
        try:
            if self.page:
                try:
                    self.page.close()
                except Exception:
                    pass
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._playwright = None
            self.context = None
            self.page = None
            self.is_running = False
            self._thread_id = None

    def is_logged_in(self) -> bool:
        """Verifies if the current browser session has an authenticated Reddit account."""
        if not self.ensure_active():
            return False

        try:
            # Check current cookies first
            cookies = self.context.cookies()
            has_session_cookie = any(c.get("name") in ("reddit_session", "token") for c in cookies)

            # Navigate to Reddit home if not on reddit
            if "reddit.com" not in self.page.url:
                self.page.goto("https://www.reddit.com/", timeout=25000, wait_until="commit")
                time.sleep(2.0)

            # Check DOM for logged-in UI elements
            logged_in_selectors = [
                'button#USER_DROPDOWN_ID',
                'shreddit-user-dropdown-button',
                '[data-testid="user-dropdown"]',
                'a[href*="/user/"]',
                'button[aria-label*="User Menu"]',
            ]
            for sel in logged_in_selectors:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    return True

            # Check for login/sign-up buttons (which indicate logged out)
            logged_out = self.page.query_selector('a[href*="/login"], a[href*="/register"]')
            if logged_out and logged_out.is_visible():
                return False

            return has_session_cookie
        except Exception as e:
            add_log("WARN", f"Session check notice: {str(e)[:100]}")
            return False

    def post_image_to_subreddit(
        self,
        subreddit: str,
        title: str,
        image_path: str,
        first_comment: Optional[str] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Submits an image post to a specified subreddit with a title and optional follow-up comment.
        """
        clean_sub = subreddit.replace("r/", "").strip()

        # Validate image file
        if not os.path.exists(image_path):
            err = f"Image file not found: {image_path}"
            add_log("ERROR", err)
            record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
            return {"success": False, "message": err}

        # Dry run simulation
        if dry_run:
            msg = f"[DRY RUN] Would submit image post to r/{clean_sub}: \"{title}\" with image {os.path.basename(image_path)}"
            add_log("INFO", msg)
            record_post(clean_sub, title, image_path, "https://reddit.com/dry-run", "dry_run", "success")
            return {"success": True, "mode": "dry_run", "message": msg}

        if not self.ensure_active():
            err = "Could not start Reddit browser controller"
            add_log("ERROR", err)
            record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
            return {"success": False, "message": err}

        # Verify authentication
        if not self.is_logged_in():
            err = "Not logged into Reddit. Please run login_reddit.bat to log into your Reddit account first."
            add_log("ERROR", f"❌ r/{clean_sub}: {err}")
            record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
            return {"success": False, "message": err, "error_type": "NOT_LOGGED_IN"}

        try:
            submit_url = f"https://www.reddit.com/r/{clean_sub}/submit"
            add_log("INFO", f"Navigating to submit page: {submit_url}")
            self.page.goto(submit_url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.5, 4.0))

            # 1. Switch to "Images & Video" tab
            image_tab_selectors = [
                'button:has-text("Images & Video")',
                'button:has-text("Images")',
                'button:has-text("Image")',
                '[data-testid="tab-images"]',
                'tab-item[value="IMAGE"]',
                'button[role="tab"]:has-text("Image")',
            ]
            tab_clicked = False
            for sel in image_tab_selectors:
                tab_btn = self.page.query_selector(sel)
                if tab_btn and tab_btn.is_visible():
                    tab_btn.click()
                    tab_clicked = True
                    time.sleep(random.uniform(1.0, 1.8))
                    break

            if not tab_clicked:
                add_log("WARN", "Could not explicitly find Image tab button; proceeding with file input detection.")

            # 2. Enter Post Title
            title_selectors = [
                'textarea[placeholder="Title"]',
                'textarea[data-testid="post-title"]',
                'input[name="title"]',
                'textarea[name="title"]',
                'input[placeholder="Title"]',
                '[role="textbox"][name="title"]'
            ]
            title_box = None
            for sel in title_selectors:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    title_box = el
                    break

            if not title_box:
                err = "Could not find Title input box on submit page"
                add_log("ERROR", f"r/{clean_sub}: {err}")
                record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
                return {"success": False, "message": err}

            title_box.click()
            time.sleep(0.5)
            # Natural human keystroke typing for title
            for ch in title:
                self.page.keyboard.type(ch)
                time.sleep(random.uniform(0.02, 0.06))

            time.sleep(random.uniform(1.0, 2.0))

            # 3. Upload Image File
            file_input = self.page.query_selector('input[type="file"]')
            if not file_input:
                err = "Could not locate file input to attach image"
                add_log("ERROR", f"r/{clean_sub}: {err}")
                record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
                return {"success": False, "message": err}

            abs_img = os.path.abspath(image_path)
            add_log("INFO", f"Uploading image card: {os.path.basename(abs_img)}")
            file_input.set_input_files(abs_img)

            # Wait for upload processing and preview thumbnail
            time.sleep(random.uniform(3.5, 5.5))

            # 4. Locate and click Post button
            post_btn_selectors = [
                'button[data-testid="post-button"]',
                'button:has-text("Post"):not([disabled])',
                'shreddit-submit-button',
                'button[type="submit"]:has-text("Post")',
            ]
            post_btn = None
            for sel in post_btn_selectors:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    post_btn = el
                    break

            if not post_btn:
                err = "Post button not found or remains disabled"
                add_log("ERROR", f"r/{clean_sub}: {err}")
                record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
                return {"success": False, "message": err}

            add_log("INFO", f"Submitting post to r/{clean_sub}...")
            post_btn.click()

            # 5. Wait for redirect to published post page
            time.sleep(random.uniform(4.0, 7.0))
            published_url = self.page.url

            # Verify URL is no longer on /submit
            if "/submit" in published_url:
                # Check for error banners
                error_banner = self.page.query_selector('[role="alert"], [data-testid="alert"]')
                banner_txt = error_banner.inner_text().strip() if error_banner else "Unknown submit error"
                err = f"Post not accepted by Reddit: {banner_txt}"
                add_log("ERROR", f"r/{clean_sub}: {err}")
                record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
                return {"success": False, "message": err}

            add_log("SUCCESS", f"✅ Successfully published image post to r/{clean_sub}! URL: {published_url}")

            # 6. Optional: Submit First Comment (Markdown breakdown + Discord CTA)
            first_comment_status = "none"
            if first_comment and first_comment.strip():
                try:
                    time.sleep(random.uniform(2.0, 3.5))
                    comment_box_selectors = [
                        'div[role="textbox"]',
                        'div[data-testid="comment-box"]',
                        'div[contenteditable="true"]',
                        'textarea[placeholder*="comment"]',
                    ]
                    comment_box = None
                    for sel in comment_box_selectors:
                        el = self.page.query_selector(sel)
                        if el and el.is_visible():
                            comment_box = el
                            break

                    if comment_box:
                        comment_box.click()
                        time.sleep(0.5)
                        self.page.keyboard.type(first_comment.strip())
                        time.sleep(1.0)

                        submit_comment_btn = self.page.query_selector('button:has-text("Comment"), button[type="submit"]')
                        if submit_comment_btn and submit_comment_btn.is_visible():
                            submit_comment_btn.click()
                            time.sleep(2.0)
                            first_comment_status = "posted"
                            add_log("INFO", f"Posted first comment & Discord CTA on r/{clean_sub} post.")
                except Exception as c_err:
                    first_comment_status = f"failed: {str(c_err)[:60]}"
                    add_log("WARN", f"Could not post first comment ({str(c_err)[:80]})")

            record_post(
                clean_sub, title, image_path, published_url, "live", "success",
                first_comment_status=first_comment_status
            )
            return {
                "success": True,
                "post_url": published_url,
                "first_comment": first_comment_status == "posted"
            }

        except Exception as ex:
            err = f"Exception during Reddit submission: {str(ex)[:150]}"
            add_log("ERROR", f"r/{clean_sub}: {err}")
            record_post(clean_sub, title, image_path, None, "live", "failed", error_message=err)
            return {"success": False, "message": err}


controller = RedditBrowserController()
