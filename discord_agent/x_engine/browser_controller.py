import os
import time
import random
import urllib.parse
from typing import List, Dict, Any, Optional
from playwright.sync_api import sync_playwright, BrowserContext, Page
try:
    from .config import config
    from .database import add_log, is_user_restricted, add_restricted_user
except (ImportError, ValueError):
    from config import config
    from database import add_log, is_user_restricted, add_restricted_user

class BrowserController:
    def __init__(self):
        self._playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False

    def is_alive(self) -> bool:
        """Verifies if the browser and page are still alive and responsive."""
        if not self.is_running or not self.context or not self.page:
            return False
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    def ensure_active(self, headless: Optional[bool] = None) -> bool:
        """Ensures a live browser session is ready, auto-recovering if closed."""
        if not self.is_alive():
            self.stop()
            return self.start(headless=headless)
        return True

    def start(self, headless: Optional[bool] = None) -> bool:
        """Starts Playwright with persistent context to preserve login state."""
        if self.is_alive():
            return True

        self.stop()  # Clean any stale resources

        use_headless = config.headless if headless is None else headless
        data_dir = config.browser_data_dir
        if not os.path.isabs(data_dir):
            data_dir = os.path.join(os.path.dirname(__file__), data_dir)
        data_dir = os.path.abspath(data_dir)
        os.makedirs(data_dir, exist_ok=True)

        try:
            self._playwright = sync_playwright().start()

            # Anti-detection & cloud container ultra-low-memory flags (tuned for 512MB RAM constraint)
            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",  # CRITICAL for Render: prevents 64MB /dev/shm freeze & crash
                "--disable-gpu",            # Saves 100MB+ RAM in cloud headless mode
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--mute-audio",
                "--no-first-run",
            ]

            launch_kwargs = {
                "user_data_dir": data_dir,
                "headless": use_headless,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 850},
                "args": args,
            }

            def _try_launch():
                # On Windows local dev, prefer system Chrome if available; on Linux/cloud, launch Chromium directly
                if os.name == "nt":
                    try:
                        return self._playwright.chromium.launch_persistent_context(
                            channel="chrome",
                            **launch_kwargs
                        )
                    except Exception:
                        pass
                return self._playwright.chromium.launch_persistent_context(**launch_kwargs)

            try:
                self.context = _try_launch()
            except Exception as launch_err:
                err_msg = str(launch_err)
                if "Executable doesn't exist" in err_msg or "playwright install" in err_msg:
                    add_log("ERROR", "❌ Chromium browser binary is not pre-installed in this container environment. "
                                     "Deploy using Dockerfile on Render so browser dependencies are baked in at build time.")
                elif "shared libraries" in err_msg or "libnss3" in err_msg or "libgbm" in err_msg:
                    add_log("ERROR", f"❌ Missing Linux OS graphics libraries for Chromium ({err_msg[:120]}). Deploy with Dockerfile on Render.")
                else:
                    add_log("ERROR", f"❌ Chromium launch error: {err_msg[:180]}")
                self.stop()
                return False

            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

            # Anti-detection stealth script injection (mask webdriver & automation fingerprints)
            stealth_js = """
            // 1. Delete and mask navigator.webdriver
            try {
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                delete Object.getPrototypeOf(navigator).webdriver;
            } catch (e) {}

            // 2. Realistic languages array
            try {
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            } catch (e) {}

            // 3. Realistic plugins mock
            try {
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                    ]
                });
            } catch (e) {}

            // 4. Chrome runtime, csi, and loadTimes mock
            try {
                if (!window.chrome) {
                    window.chrome = {};
                }
                if (!window.chrome.runtime) {
                    window.chrome.runtime = {
                        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
                        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
                        RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' }
                    };
                }
                if (!window.chrome.loadTimes) {
                    window.chrome.loadTimes = function() {
                        return {
                            requestTime: performance.now() / 1000,
                            startLoadTime: performance.now() / 1000,
                            commitLoadTime: performance.now() / 1000,
                            finishDocumentLoadTime: performance.now() / 1000,
                            finishLoadTime: performance.now() / 1000,
                            firstPaintTime: performance.now() / 1000,
                            firstPaintAfterLoadTime: 0,
                            navigationType: 'Other'
                        };
                    };
                }
                if (!window.chrome.csi) {
                    window.chrome.csi = function() {
                        return { startE: Date.now(), onloadT: Date.now(), pageT: performance.now(), tran: 15 };
                    };
                }
            } catch (e) {}

            // 5. Hardware concurrency & device memory
            try {
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            } catch (e) {}

            // 6. WebGL Vendor & Renderer Unmasking (Masks SwiftShader/Mesa bot fingerprint)
            try {
                const getParameterOriginal = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) { // UNMASKED_VENDOR_WEBGL
                        return 'Intel Inc.';
                    }
                    if (parameter === 37446) { // UNMASKED_RENDERER_WEBGL
                        return 'Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0';
                    }
                    return getParameterOriginal.apply(this, arguments);
                };
                if (window.WebGL2RenderingContext) {
                    const getParameter2Original = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) {
                            return 'Intel Inc.';
                        }
                        if (parameter === 37446) {
                            return 'Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0';
                        }
                        return getParameter2Original.apply(this, arguments);
                    };
                }
            } catch (e) {}

            // 7. Notification Permissions Query Mock
            try {
                if (window.navigator.permissions) {
                    const origPermissionsQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = function(parameters) {
                        if (parameters && parameters.name === 'notifications') {
                            return Promise.resolve({ state: Notification.permission });
                        }
                        return origPermissionsQuery.apply(this, arguments);
                    };
                }
            } catch (e) {}

            // 8. Outer window dimensions normalization
            try {
                if (window.outerWidth === 0 && window.outerHeight === 0) {
                    Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth + 16 });
                    Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 80 });
                }
            } catch (e) {}
            """
            try:
                self.context.add_init_script(stealth_js)
            except Exception as _st_err:
                pass

            # If X_AUTH_TOKEN is configured, inject cookie directly
            if config.x_auth_token and config.x_auth_token.strip():
                token = config.x_auth_token.strip()
                self.context.add_cookies([
                    {"name": "auth_token", "value": token, "domain": ".x.com", "path": "/", "httpOnly": True, "secure": True},
                    {"name": "auth_token", "value": token, "domain": ".twitter.com", "path": "/", "httpOnly": True, "secure": True}
                ])
                add_log("INFO", "Injected X auth_token cookie into browser session.")

            # Low-memory optimization: abort heavy decorative images, video streams & fonts to conserve RAM on Render
            def _block_heavy_assets(route):
                req = route.request
                url = req.url
                # Always allow critical endpoints like image upload, tweet posting, and API requests
                if "upload" in url or "CreateTweet" in url or "graphql" in url or "api" in url:
                    route.continue_()
                    return
                # Abort heavy media, image bitmaps, and fonts to save 180MB+ RAM during tweet scans
                if req.resource_type in ("image", "media", "font", "images"):
                    route.abort()
                    return
                route.continue_()

            try:
                self.context.route("**/*", _block_heavy_assets)
            except Exception:
                pass

            self.is_running = True
            add_log("INFO", f"Browser controller started with stealth anti-detect (headless={use_headless})")
            return True
        except Exception as e:
            add_log("ERROR", f"Failed to start browser: {str(e)}")
            self.stop()
            return False

    def stop(self):
        """Closes browser and cleans up resources cleanly."""
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

        self.context = None
        self.page = None
        self._playwright = None
        self.is_running = False

    def is_logged_in(self) -> bool:
        """Checks if the current session is logged into X."""
        status_info = self.check_account_status()
        return status_info.get("logged_in", False)

    def _dismiss_popups_and_modals(self):
        """Dismisses GDPR cookie banners, notification prompts, and overlay dialogs."""
        if not self.page:
            return
        selectors = [
            'button:has-text("Refuse non-essential cookies")',
            'button:has-text("Accept all cookies")',
            'button:has-text("Accept all")',
            'button:has-text("Not now")',
            'button:has-text("Maybe later")',
            '[data-testid="app-bar-close"]',
            '[aria-label="Close"]',
            'button:has-text("Dismiss")',
            'button:has-text("Got it")'
        ]
        for sel in selectors:
            try:
                elem = self.page.query_selector(sel)
                if elem and elem.is_visible():
                    add_log("INFO", f"Dismissed X overlay popup ({sel}).")
                    elem.click()
                    time.sleep(1.0)
            except Exception:
                pass

    def check_account_status(self) -> Dict[str, Any]:
        """Checks if the current session is active, suspended, or locked on X."""
        if not self.ensure_active():
            return {"logged_in": False, "status": "browser_inactive", "message": "Browser inactive"}
        try:
            try:
                self.page.goto("https://x.com/home", timeout=35000, wait_until="domcontentloaded")
            except Exception:
                pass
            time.sleep(3.0)
            self._dismiss_popups_and_modals()

            account_btn = self.page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Profile_Link"]')
            title = getattr(self.page, "title", lambda: "")()
            is_authed = bool(account_btn or "Home / X" in title or (title.startswith("(") and "/ X" in title))

            if not is_authed:
                curr_url = getattr(self.page, "url", "unknown")
                return {"logged_in": False, "status": "not_logged_in", "message": f"Not logged in (URL: {curr_url} | Title: {title})"}

            body_text = self.page.inner_text("body").lower()
            if "your account is suspended" in body_text or "permanently in read-only mode" in body_text:
                return {
                    "logged_in": True,
                    "status": "suspended",
                    "message": "Your account is suspended by X (read-only mode). Cannot post tweets or media."
                }
            if "your account has been locked" in body_text or "confirm your email" in body_text:
                return {
                    "logged_in": True,
                    "status": "locked",
                    "message": "Your account is locked / requires verification."
                }

            return {"logged_in": True, "status": "active", "message": "Account active and healthy"}
        except Exception as e:
            add_log("WARN", f"Session check encountered: {str(e)}")
            return {"logged_in": False, "status": "error", "message": str(e)}

    def search_tweets(self, query: str, max_results: int = 10, retry: bool = True) -> List[Dict[str, Any]]:
        """
        Searches X for the query (using 'Live' tab, with automatic fallback to 'Top' tab)
        and extracts tweet elements with auto-recovery.
        """
        if not self.ensure_active():
            return []

        results: List[Dict[str, Any]] = []
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://x.com/search?q={encoded_query}&f=live"

        try:
            add_log("INFO", f"Scanning X for: '{query}'")
            try:
                self.page.goto(search_url, timeout=35000, wait_until="domcontentloaded")
            except Exception:
                pass

            time.sleep(2.0)
            self._dismiss_popups_and_modals()

            # Explicitly wait for tweets to render
            try:
                self.page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
            except Exception:
                pass

            # Scroll to trigger dynamic tweet hydration
            for _ in range(2):
                self.page.mouse.wheel(0, 500)
                time.sleep(1.0)

            articles = self.page.query_selector_all('article[data-testid="tweet"]')
            if not articles:
                self._dismiss_popups_and_modals()
                curr_url = getattr(self.page, "url", "unknown")
                curr_title = getattr(self.page, "title", lambda: "unknown")()
                body_snippet = self.page.inner_text("body")[:220].replace("\n", " ").strip()
                add_log("WARN", f"0 tweets on live tab for '{query}' (URL: {curr_url} | Title: {curr_title} | Body: {body_snippet[:100]}...)")
                
                # Check for Retry button
                try:
                    retry_btn = self.page.query_selector('button:has-text("Retry"), [role="button"]:has-text("Retry")')
                    if retry_btn:
                        add_log("INFO", "Found X 'Retry' button, clicking...")
                        retry_btn.click()
                        time.sleep(4.0)
                        articles = self.page.query_selector_all('article[data-testid="tweet"]')
                except Exception:
                    pass

                # If still 0, try the Top tab (often much more reliable on cloud IPs)
                if not articles:
                    top_url = f"https://x.com/search?q={encoded_query}"
                    add_log("INFO", f"Checking Top tab for '{query}'...")
                    try:
                        self.page.goto(top_url, timeout=30000, wait_until="domcontentloaded")
                        time.sleep(2.0)
                        self._dismiss_popups_and_modals()
                        try:
                            self.page.wait_for_selector('article[data-testid="tweet"]', timeout=8000)
                        except Exception:
                            pass
                        articles = self.page.query_selector_all('article[data-testid="tweet"]')
                    except Exception:
                        pass
            for art in articles:
                if len(results) >= max_results:
                    break

                try:
                    # Skip if this tweet is a reply or comment in a thread
                    art_text = art.inner_text()
                    if "Replying to @" in art_text or "replying to @" in art_text:
                        continue

                    # Extract Tweet status URL and ID
                    status_link = art.query_selector('a[href*="/status/"]')
                    if not status_link:
                        continue

                    href = status_link.get_attribute("href")
                    if not href or "/status/" not in href:
                        continue

                    tweet_id = href.split("/status/")[1].split("?")[0].split("/")[0]
                    tweet_url = f"https://x.com{href}" if href.startswith("/") else href

                    # Extract Author handle, display name & badges
                    author = "unknown"
                    display_name = ""
                    badge_label = ""
                    user_elem = art.query_selector('[data-testid="User-Name"]')
                    if user_elem:
                        user_text = user_elem.inner_text()
                        display_name = user_text
                        for part in user_text.split():
                            if part.startswith("@"):
                                author = part.replace("@", "")
                                break
                        badge_elem = user_elem.query_selector('svg[data-testid="icon-verified"], [aria-label*="Government"], [aria-label*="Official"], [aria-label*="State-affiliated"]')
                        if badge_elem:
                            badge_label = badge_elem.get_attribute("aria-label") or ""

                    # RESTRICTED USER FILTER: Check if user was previously flagged as having restricted replies
                    if is_user_restricted(author):
                        continue

                    # PROTECTED ACCOUNT FILTER: Skip private/protected accounts (lock icon)
                    lock_elem = art.query_selector('svg[data-testid="icon-lock"], [aria-label*="Protected"]')
                    if lock_elem:
                        add_restricted_user(author, reason="protected_account")
                        continue

                    # FEED-LEVEL REPLY RESTRICTION FILTER: Check if reply button is disabled or restricted
                    reply_btn_feed = art.query_selector('button[data-testid="reply"]')
                    if reply_btn_feed:
                        is_disabled = (
                            reply_btn_feed.get_attribute("disabled") is not None
                            or reply_btn_feed.get_attribute("aria-disabled") == "true"
                        )
                        if is_disabled:
                            add_restricted_user(author, reason="replies_restricted_on_feed")
                            continue


                    # Extract Tweet Text
                    text_elem = art.query_selector('[data-testid="tweetText"]')
                    tweet_text = text_elem.inner_text() if text_elem else ""

                    if tweet_id and tweet_text:
                        results.append({
                            "tweet_id": tweet_id,
                            "author": author,
                            "content": tweet_text,
                            "url": tweet_url,
                            "matched_keyword": query
                        })
                except Exception:
                    continue

            add_log("INFO", f"Found {len(results)} tweets for query '{query}'")
            return results

        except Exception as e:
            err_str = str(e)
            add_log("ERROR", f"Search failed for '{query}': {err_str}")
            # If browser/page was closed, auto-restart and retry once
            if retry and ("closed" in err_str.lower() or "crashed" in err_str.lower()):
                add_log("WARN", "Browser was closed. Auto-recovering session...")
                self.stop()
                if self.start():
                    return self.search_tweets(query, max_results=max_results, retry=False)
            return []

    def _move_mouse_naturally(self, target_element):
        """Simulate realistic human-like mouse movement towards an element before clicking."""
        try:
            if not self.page or not target_element:
                return
            box = target_element.bounding_box()
            if not box:
                return
            target_x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
            target_y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
            steps = random.randint(16, 28)
            self.page.mouse.move(target_x, target_y, steps=steps)
            time.sleep(random.uniform(0.15, 0.35))
        except Exception:
            pass

    def _simulate_reading_dwell(self):
        """Simulate human reading time by scrolling down and back slightly."""
        try:
            if not self.page:
                return
            # Read tweet: smooth scroll down
            scroll_amount = random.randint(180, 360)
            self.page.mouse.wheel(0, scroll_amount)
            time.sleep(random.uniform(2.0, 3.5))
            # Slight bounce back
            self.page.mouse.wheel(0, -random.randint(40, 90))
            time.sleep(random.uniform(0.8, 1.6))
        except Exception:
            pass

    def post_reply(
        self,
        tweet_id: str,
        tweet_url: str,
        reply_text: str,
        image_path: Optional[str] = None,
        dry_run: bool = True,
        retry: bool = True
    ) -> Dict[str, Any]:
        """
        Posts a reply to a tweet with humanized dwell, typing, mouse trajectories, and anti-ban checks.
        """
        if dry_run:
            img_info = f" + [Image: {os.path.basename(image_path)}]" if image_path else ""
            add_log("INFO", f"[DRY RUN] Would reply to {tweet_url}: \"{reply_text}\"{img_info}")
            return {"success": True, "mode": "dry_run", "message": "Dry run simulated successfully", "image_attached": bool(image_path)}

        if not self.ensure_active():
            return {"success": False, "mode": "live", "message": "Browser is not running and could not be started"}

        try:
            target_url = tweet_url if tweet_url else f"https://x.com/i/web/status/{tweet_id}"
            add_log("INFO", f"Navigating to tweet: {target_url}")
            try:
                self.page.goto(target_url, timeout=35000, wait_until="commit")
            except Exception:
                pass
            time.sleep(random.uniform(2.5, 4.0))

            # Simulate human reading the tweet before replying
            self._simulate_reading_dwell()

            # Look for reply input box on the tweet page
            # 1. First try direct inline reply box
            reply_box = self.page.query_selector('[data-testid="tweetTextarea_0"]')

            # 2. If not visible, click the reply icon button on the article
            if not reply_box:
                reply_btn = self.page.query_selector('article button[data-testid="reply"]')
                if reply_btn:
                    self._move_mouse_naturally(reply_btn)
                    reply_btn.click()
                    time.sleep(random.uniform(1.2, 2.0))
                    reply_box = self.page.query_selector('[data-testid="tweetTextarea_0"]')

            if not reply_box:
                # Also try generic textbox role
                reply_box = self.page.query_selector('div[role="textbox"]')

            if not reply_box:
                # Extract author from page and add to restricted list so future tweets are auto-skipped
                author_hdr = self.page.query_selector('[data-testid="User-Name"]')
                if author_hdr:
                    for part in author_hdr.inner_text().split():
                        if part.startswith("@"):
                            flagged_user = part.replace("@", "")
                            add_restricted_user(flagged_user, reason="replies_restricted")
                            add_log("INFO", f"Added @{flagged_user} to restricted users list (author restricts replies).")
                            break

                page_text = self.page.inner_text("body").lower()
                if "your account is suspended" in page_text or "permanently in read-only mode" in page_text:
                    err_msg = "Account is suspended by X (permanently in read-only mode). Action rejected by X."
                    add_log("ERROR", err_msg)
                    return {"success": False, "mode": "live", "message": err_msg, "error_type": "ACCOUNT_SUSPENDED"}

                if any(w in page_text for w in ["confirm that you're the account owner", "verify your identity", "your account has been locked"]):
                    err_msg = "Account verification challenge triggered by X."
                    add_log("ERROR", f"CRITICAL: {err_msg}")
                    return {"success": False, "mode": "live", "message": err_msg, "error_type": "ACCOUNT_CHALLENGE"}

                if "these posts are protected" in page_text or "only confirmed followers" in page_text:
                    err_msg = "Skipped: Post or account is protected / private."
                    add_log("INFO", f"Tweet {tweet_id}: {err_msg}")
                    return {"success": False, "mode": "live", "message": err_msg, "reason": "restricted"}

                if "who can reply" in page_text or "can reply" in page_text or "replies are limited" in page_text:
                    err_msg = "Skipped: Post author restricted replies (e.g. only accounts they follow or verified users can reply)."
                    add_log("INFO", f"Tweet {tweet_id}: {err_msg}")
                    return {"success": False, "mode": "live", "message": err_msg, "reason": "restricted"}

                err_msg = "Could not find reply input box (Post replies restricted by author or thread closed)."
                add_log("INFO", f"Tweet {tweet_id}: {err_msg}")
                return {"success": False, "mode": "live", "message": err_msg, "reason": "restricted"}

            # Move mouse naturally and focus reply box
            self._move_mouse_naturally(reply_box)
            reply_box.click()
            time.sleep(random.uniform(0.6, 1.2))

            # Type naturally with human-like variable keystroke delay and hesitation pauses
            for char in reply_text:
                self.page.keyboard.type(char)
                if char in ('.', ',', '!', '?', ' ') and random.random() < 0.22:
                    time.sleep(random.uniform(0.18, 0.45))
                else:
                    time.sleep(random.uniform(0.035, 0.095))

            time.sleep(random.uniform(1.2, 2.5))

            # If an image is provided, attach it to the reply
            image_attached = False
            upload_error = None
            if image_path and os.path.exists(image_path):
                try:
                    add_log("INFO", f"Attaching image to reply: {os.path.basename(image_path)}")

                    def on_upload_response(response):
                        nonlocal upload_error
                        if "upload.json" in response.url:
                            if response.status >= 400:
                                try:
                                    res_json = response.json()
                                    upload_error = res_json.get("errors", [{}])[0].get("message", f"HTTP {response.status}")
                                except Exception:
                                    upload_error = f"Upload error {response.status}"

                    self.page.on("response", on_upload_response)

                    # Find X's media file upload input (hidden input[type='file'])
                    file_input = self.page.query_selector('input[data-testid="fileInput"], input[type="file"]')
                    if file_input:
                        file_input.set_input_files(os.path.abspath(image_path))
                        # Give X time to process and render the image preview
                        time.sleep(random.uniform(2.5, 4.0))

                        try:
                            self.page.remove_listener("response", on_upload_response)
                        except Exception:
                            pass

                        # Check if upload was blocked by X
                        page_body = self.page.inner_text("body").lower()
                        if "some of your media failed to upload" in page_body:
                            upload_error = upload_error or "Some of your media failed to upload."

                        if upload_error:
                            add_log("ERROR", f"X media upload failed: {upload_error}")
                            if "suspended" in upload_error.lower() or "not permitted" in upload_error.lower():
                                return {"success": False, "mode": "live", "message": f"Account suspended: {upload_error}", "error_type": "ACCOUNT_SUSPENDED"}
                            return {"success": False, "mode": "live", "message": f"Media upload rejected by X: {upload_error}"}
                        else:
                            try:
                                self.page.wait_for_selector(
                                    '[data-testid="attachments"], [data-testid="removeMedia"], button[aria-label*="Remove"]',
                                    timeout=6000
                                )
                                image_attached = True
                                add_log("INFO", f"Image attached successfully ({os.path.basename(image_path)}).")
                            except Exception:
                                image_attached = False
                                add_log("WARN", f"Could not verify image attachment ({os.path.basename(image_path)}).")
                    else:
                        add_log("WARN", "Could not locate file input to attach image on page.")
                except Exception as img_err:
                    add_log("WARN", f"Failed to attach image ({str(img_err)}). Continuing with text reply.")

            time.sleep(random.uniform(1.2, 2.0))

            # Find Post / Reply button (prioritize exact inline reply button)
            submit_btn = self.page.query_selector('[data-testid="tweetButtonInline"]')
            if not submit_btn:
                submit_btn = self.page.query_selector('[data-testid="tweetButton"]')

            if not submit_btn:
                err_msg = "Reply content entered but could not locate submit button."
                add_log("ERROR", err_msg)
                return {"success": False, "mode": "live", "message": err_msg}

            # Check if button is enabled
            if submit_btn.is_disabled():
                err_msg = "Submit reply button is disabled."
                add_log("ERROR", err_msg)
                return {"success": False, "mode": "live", "message": err_msg}

            # Intercept CreateTweet GraphQL response to verify actual post success
            create_tweet_api = {}
            def on_create_tweet_response(res):
                if "CreateTweet" in res.url:
                    create_tweet_api["status"] = res.status
                    try:
                        create_tweet_api["json"] = res.json()
                    except Exception:
                        create_tweet_api["text"] = res.text()[:300]

            self.page.on("response", on_create_tweet_response)

            try:
                # Move mouse naturally over submit button and pause before clicking
                self._move_mouse_naturally(submit_btn)
                time.sleep(random.uniform(1.2, 2.2))
                submit_btn.click()
                time.sleep(random.uniform(2.5, 4.0))
            finally:
                try:
                    self.page.remove_listener("response", on_create_tweet_response)
                except Exception:
                    pass

            # 1. Verify GraphQL API response
            if create_tweet_api:
                if create_tweet_api.get("json") and "errors" in create_tweet_api["json"]:
                    err_msg = create_tweet_api["json"]["errors"][0].get("message", "API Error")
                    add_log("ERROR", f"X API rejected reply: {err_msg}")
                    if "TwitterUserNotSuspended" in err_msg or "suspended" in err_msg.lower():
                        return {"success": False, "mode": "live", "message": f"Account suspended: {err_msg}", "error_type": "ACCOUNT_SUSPENDED"}
                    return {"success": False, "mode": "live", "message": f"Post rejected by X: {err_msg}"}

            # 2. Check for challenge or lockout modals
            try:
                page_body_after = self.page.inner_text("body").lower()
                if any(w in page_body_after for w in ["confirm that you're the account owner", "verify your identity", "your account has been locked", "unusual activity"]):
                    err_msg = "X security verification challenge detected (Arkose/Lock)."
                    add_log("ERROR", f"CRITICAL: {err_msg}")
                    return {"success": False, "mode": "live", "message": err_msg, "error_type": "ACCOUNT_CHALLENGE"}
            except Exception:
                pass

            # 3. Check for error toast on screen
            toast = self.page.query_selector('[data-testid="toast"], [role="alert"]')
            if toast:
                toast_msg = toast.inner_text().strip()
                if any(w in toast_msg.lower() for w in ["not allowed", "not be allowed", "something went wrong", "suspended", "unusual activity"]):
                    add_log("ERROR", f"X error toast: {toast_msg}")
                    if "not allowed" in toast_msg.lower() or "suspended" in toast_msg.lower():
                        return {"success": False, "mode": "live", "message": f"Account restricted: {toast_msg}", "error_type": "ACCOUNT_SUSPENDED"}
                    return {"success": False, "mode": "live", "message": f"X error: {toast_msg}"}

            # 4. Check if reply box still exists with text (meaning submission did not go through)
            reply_box_after = self.page.query_selector('[data-testid="tweetTextarea_0"]')
            if reply_box_after and reply_box_after.is_visible():
                text_remaining = reply_box_after.inner_text().strip()
                if text_remaining:
                    err_msg = "Reply box still contains text after submit; tweet was not published by X."
                    add_log("ERROR", err_msg)
                    return {"success": False, "mode": "live", "message": err_msg}

            img_status = f" + image [{os.path.basename(image_path)}]" if (image_path and image_attached) else ""
            add_log("SUCCESS", f"Live comment successfully posted to tweet {tweet_id}{img_status}!")
            return {"success": True, "mode": "live", "message": "Reply published", "image_attached": image_attached}

        except Exception as e:
            err_msg = str(e)
            add_log("ERROR", f"Failed to post reply: {err_msg}")
            # If browser/page was closed, auto-recover and retry once
            if retry and ("closed" in err_msg.lower() or "crashed" in err_msg.lower()):
                add_log("WARN", "Browser disconnected during reply. Auto-recovering session...")
                self.stop()
                if self.start():
                    return self.post_reply(tweet_id, tweet_url, reply_text, image_path=image_path, dry_run=dry_run, retry=False)
            return {"success": False, "mode": "live", "message": f"Failed to post reply: {err_msg}"}

browser_controller = BrowserController()
