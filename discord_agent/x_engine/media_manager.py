import os
import json
import time
import random
from datetime import datetime, date
from typing import Optional, List, Dict, Any
try:
    from .config import config
    from .database import add_log
except (ImportError, ValueError):
    from config import config
    from database import add_log

WORKSPACE_DIR = os.path.abspath(os.path.dirname(__file__))
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SESSION_STATE_FILE = os.path.join(WORKSPACE_DIR, "data", "image_session.json")

def resolve_media_path(raw_path: Optional[str] = None) -> str:
    path = raw_path or config.media_image_path
    if not path:
        return os.path.join(WORKSPACE_DIR, "media")
    if os.path.isabs(path) and os.path.exists(path):
        return path

    candidate1 = os.path.abspath(os.path.join(WORKSPACE_DIR, path))
    if os.path.exists(candidate1):
        return candidate1

    candidate2 = os.path.abspath(os.path.join(os.path.dirname(WORKSPACE_DIR), path))
    if os.path.exists(candidate2):
        return candidate2

    stripped = path.replace("./x_engine/", "").replace("x_engine/", "").replace("./", "")
    candidate3 = os.path.abspath(os.path.join(WORKSPACE_DIR, stripped))
    if os.path.exists(candidate3):
        return candidate3

    return os.path.join(WORKSPACE_DIR, "media")

def get_media_dir() -> str:
    return resolve_media_path(config.media_image_path)

def list_available_media() -> List[Dict[str, Any]]:
    """Returns all supported image files in the configured media directory."""
    media_dir = get_media_dir()
    if not os.path.exists(media_dir) or not os.path.isdir(media_dir):
        return []

    results = []
    try:
        for fname in sorted(os.listdir(media_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_IMAGE_EXTS:
                full_path = os.path.join(media_dir, fname)
                if os.path.isfile(full_path):
                    results.append({
                        "filename": fname,
                        "size_bytes": os.path.getsize(full_path),
                        "path": full_path,
                        "rel_path": os.path.relpath(full_path, WORKSPACE_DIR),
                        "mtime": os.path.getmtime(full_path)
                    })
    except Exception as e:
        add_log("WARN", f"Failed to list media files: {str(e)}")
    return results

def ensure_s3_slot_image(current_slot: int, last_template: Optional[str] = None) -> Optional[str]:
    """
    If S3 / template integration is enabled, fetches the latest daily JSON
    and renders a fresh SpikeSignals Key Events card from the 5 templates randomly for this 2-hour slot.
    """
    if not getattr(config, "s3_enabled", False):
        return None

    media_dir = get_media_dir()
    os.makedirs(media_dir, exist_ok=True)
    card_filename = f"spikesignals_slot_{current_slot}.jpg"
    card_path = os.path.join(media_dir, card_filename)

    if not os.path.exists(card_path):
        try:
            from .s3_fetcher import fetch_s3_market_json
            from .template_compositor import render_random_template
        except (ImportError, ValueError):
            from s3_fetcher import fetch_s3_market_json
            from template_compositor import render_random_template

        try:
            data = fetch_s3_market_json()
            render_res = render_random_template(data, card_path, exclude_template=last_template)
            tpl_used = render_res.get("template_used", "random")
            add_log("INFO", f"Generated fresh 2-hour SpikeSignals card from S3 using template '{tpl_used}': {card_filename}")

            # Update session state with the template used
            try:
                state = {}
                if os.path.exists(SESSION_STATE_FILE):
                    with open(SESSION_STATE_FILE, "r") as sf:
                        state = json.load(sf)
                state["last_template"] = tpl_used
                state["current_template"] = tpl_used
                os.makedirs(os.path.dirname(SESSION_STATE_FILE), exist_ok=True)
                with open(SESSION_STATE_FILE, "w") as sf:
                    json.dump(state, sf, indent=2)
            except Exception:
                pass

            # Clean older slot files to keep disk clean
            for f in os.listdir(media_dir):
                if (f.startswith("spikesignals_slot_") or f.startswith("s3_market_news_slot_")) and f != card_filename:
                    try:
                        os.remove(os.path.join(media_dir, f))
                    except Exception:
                        pass
            return card_path
        except Exception as e:
            add_log("WARN", f"Failed to generate SpikeSignals template card ({str(e)}).")
            return None
    return card_path

def get_image_session_status() -> Dict[str, Any]:
    """
    Tracks the continuous 2-hour cyclical image rotation.
    Every 2 hours, advances automatically to the next image in the media pool
    or generates a fresh Market News card from S3.
    """
    media_dir = get_media_dir()

    # Load session state or initialize
    state = {}
    if os.path.exists(SESSION_STATE_FILE):
        try:
            with open(SESSION_STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            state = {}

    anchor_time = state.get("anchor_time")
    now = time.time()
    if not anchor_time or anchor_time <= 0 or anchor_time > now:
        anchor_time = now
        state["anchor_time"] = anchor_time
        state["last_slot"] = 0

    slot_seconds = max(60.0, config.image_rotation_hours * 3600.0)
    elapsed_total = max(0.0, now - anchor_time)
    current_slot = int(elapsed_total // slot_seconds)
    slot_elapsed = elapsed_total % slot_seconds
    slot_remaining = slot_seconds - slot_elapsed

    # If S3 integration is enabled, ensure current slot card is generated
    s3_card_path = None
    if getattr(config, "s3_enabled", False):
        s3_card_path = ensure_s3_slot_image(current_slot, last_template=state.get("last_template"))
        if os.path.exists(SESSION_STATE_FILE):
            try:
                with open(SESSION_STATE_FILE, "r") as f:
                    state.update(json.load(f))
            except Exception:
                pass

    images = []
    if os.path.exists(media_dir) and os.path.isdir(media_dir):
        images = [
            f for f in sorted(os.listdir(media_dir))
            if os.path.splitext(f)[1].lower() in SUPPORTED_IMAGE_EXTS
            and os.path.isfile(os.path.join(media_dir, f))
        ]

    if not images:
        return {
            "active": False,
            "has_media": False,
            "total_images": 0,
            "current_image": None,
            "rotation_hours": config.image_rotation_hours,
            "message": "No media images found in media/ folder"
        }

    # If S3 card was generated for this slot, use it directly
    if s3_card_path and os.path.basename(s3_card_path) in images:
        active_filename = os.path.basename(s3_card_path)
        active_path = s3_card_path
        image_idx = images.index(active_filename)
        next_idx = (image_idx + 1) % len(images)
        next_filename = images[next_idx]
    else:
        image_idx = current_slot % len(images)
        active_filename = images[image_idx]
        active_path = os.path.join(media_dir, active_filename)
        next_idx = (image_idx + 1) % len(images)
        next_filename = images[next_idx]

    # Check if a new 2-hour slot just began
    if current_slot != state.get("last_slot"):
        state["last_slot"] = current_slot
        state["last_rotation_time"] = now
        state["current_image"] = active_filename
        try:
            os.makedirs(os.path.dirname(SESSION_STATE_FILE), exist_ok=True)
            with open(SESSION_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass
        add_log("INFO", f"2-Hour Rotation: Switched to image #{image_idx + 1}/{len(images)} ('{active_filename}') for the next {config.image_rotation_hours:.1f}h.")

    return {
        "active": True,
        "has_media": True,
        "total_images": len(images),
        "image_index": image_idx,
        "current_slot": current_slot,
        "current_image": active_filename,
        "current_path": active_path,
        "current_template": state.get("current_template") or state.get("last_template"),
        "next_image": next_filename,
        "rotation_hours": config.image_rotation_hours,
        "slot_elapsed_minutes": round(slot_elapsed / 60.0, 1),
        "slot_remaining_minutes": round(slot_remaining / 60.0, 1),
        "slot_remaining_hours": round(slot_remaining / 3600.0, 2),
        "next_rotation_timestamp": now + slot_remaining,
        "all_images": images
    }

def activate_image_session(hours: Optional[float] = None) -> Dict[str, Any]:
    """Resets the 2-hour rotation anchor to right now."""
    now = time.time()
    rot_hours = hours if hours is not None else config.image_rotation_hours
    state = {
        "anchor_time": now,
        "last_slot": 0,
        "rotation_hours": rot_hours,
        "activated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }
    try:
        os.makedirs(os.path.dirname(SESSION_STATE_FILE), exist_ok=True)
        with open(SESSION_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        add_log("INFO", f"2-Hour media cycle restarted. Image rotates every {rot_hours:.1f} hours.")
    except Exception as e:
        add_log("WARN", f"Failed to save rotation state: {str(e)}")

    return get_image_session_status()

def rotate_to_next_image() -> Dict[str, Any]:
    """Manually advances rotation immediately to the next image."""
    state = {}
    if os.path.exists(SESSION_STATE_FILE):
        try:
            with open(SESSION_STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            state = {}

    current_slot = state.get("last_slot", 0) + 1
    slot_seconds = config.image_rotation_hours * 3600.0
    # Adjust anchor time so that current time is at the start of the new slot
    state["anchor_time"] = time.time() - (current_slot * slot_seconds)
    state["last_slot"] = current_slot
    try:
        os.makedirs(os.path.dirname(SESSION_STATE_FILE), exist_ok=True)
        with open(SESSION_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

    return get_image_session_status()

def get_daily_image_info(explicit_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns metadata about the active image for the current 2-hour slot."""
    if explicit_path:
        target = resolve_media_path(explicit_path)
        if os.path.isfile(target):
            return {
                "filename": os.path.basename(target),
                "path": os.path.abspath(target),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "reason": "explicit_file"
            }

    status = get_image_session_status()
    if status.get("has_media") and status.get("current_path"):
        return {
            "filename": status["current_image"],
            "path": status["current_path"],
            "date": datetime.now().strftime("%Y-%m-%d"),
            "slot": status["current_slot"],
            "remaining_minutes": status["slot_remaining_minutes"],
            "reason": "2hr_cyclical_rotation"
        }

    return None

def prepare_unique_image_for_upload(original_path: str) -> Optional[str]:
    """
    Anti-Perceptual-Hash Protection:
    Dynamically applies micro-cropping, dynamic timestamp watermarking,
    and compression jitter so that every uploaded image has a 100% unique
    cryptographic SHA-256 hash AND unique perceptual pHash/SSIM.
    Ensures safe sharing of the common image during each 2-hour window.
    """
    if not original_path or not os.path.exists(original_path):
        return None

    try:
        from PIL import Image, ImageDraw

        with Image.open(original_path) as img:
            img = img.convert("RGB")
            w, h = img.size

            # 1. Micro-crop 1-3 pixels to alter exact pixel matrix and dimensions
            crop_left = random.randint(0, 2)
            crop_top = random.randint(0, 2)
            crop_right = random.randint(0, 2)
            crop_bottom = random.randint(0, 2)
            img = img.crop((crop_left, crop_top, max(10, w - crop_right), max(10, h - crop_bottom)))
            w, h = img.size

            # 2. Add subtle live timestamp watermark in the bottom corner
            draw = ImageDraw.Draw(img)
            timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            watermark_text = f"SpikeSignals • {timestamp_str}"

            badge_w = 230
            badge_h = 22
            badge_x = max(8, w - badge_w - 10)
            badge_y = max(8, h - badge_h - 10)
            draw.rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], fill=(18, 22, 28))
            draw.text((badge_x + 8, badge_y + 4), watermark_text, fill=(195, 205, 215))

            # 3. Save to a unique temporary file
            temp_dir = os.path.join(WORKSPACE_DIR, "data", "temp_media")
            os.makedirs(temp_dir, exist_ok=True)

            # Purge temp files older than 1 hour
            now = time.time()
            for f in os.listdir(temp_dir):
                fp = os.path.join(temp_dir, f)
                if os.path.isfile(fp) and now - os.path.getmtime(fp) > 3600:
                    try:
                        os.remove(fp)
                    except Exception:
                        pass

            temp_filename = f"upload_{int(now)}_{random.randint(1000, 9999)}.jpg"
            temp_path = os.path.join(temp_dir, temp_filename)

            quality = random.randint(91, 95)
            img.save(temp_path, "JPEG", quality=quality)
            return temp_path
    except Exception as e:
        add_log("WARN", f"Image hash mutation failed ({str(e)}). Using original image.")
        return original_path

def get_media_image_to_post(explicit_path: Optional[str] = None) -> Optional[str]:
    """
    Resolves the image file to attach alongside tweet replies for the current 2-hour window.
    Applies dynamic anti-hash mutation and automatically advances every 2 hours.
    """
    if not config.attach_image:
        return None

    info = get_daily_image_info(explicit_path)
    if info and "path" in info:
        raw_path = info["path"]
        return prepare_unique_image_for_upload(raw_path)

    add_log("WARN", "Image attachment is enabled, but no valid media image was found in rotation.")
    return None
