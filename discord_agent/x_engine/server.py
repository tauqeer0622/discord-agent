import os
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from config import config
from database import (
    get_stats, get_recent_posts, get_recent_comments,
    get_recent_logs, add_log
)
from engine import engine
from comment_generator import generator
from browser_controller import browser_controller
from media_manager import (
    list_available_media, get_media_dir, get_daily_image_info,
    get_image_session_status, rotate_to_next_image
)

app = FastAPI(title="X Automation Dashboard")

# Static assets directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

MEDIA_DIR = os.path.join(os.path.dirname(__file__), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
app.mount("/templates_static", StaticFiles(directory=TEMPLATES_DIR), name="templates_static")

@app.on_event("startup")
def on_startup():
    if config.auto_start:
        add_log("INFO", "Auto-start is ENABLED. Starting autonomous engine immediately with zero human intervention.")
        engine.start()

@app.on_event("shutdown")
def on_shutdown():
    if engine.state == "running":
        engine.stop()

@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse({"message": "Dashboard UI is initializing..."}, status_code=200)
    return FileResponse(index_path)

@app.get("/api/status")
def get_system_status():
    stats = get_stats()
    engine_status = engine.get_status()
    return {
        "engine": engine_status,
        "stats": stats,
        "config": {
            "company_name": config.company_name,
            "company_one_liner": config.company_one_liner,
            "company_cta": config.company_cta,
            "company_url": config.company_url,
            "target_keywords": config.target_keywords_raw,
            "negative_keywords": config.negative_keywords_raw,
            "dry_run": config.dry_run,
            "min_delay_seconds": config.min_delay_seconds,
            "max_delay_seconds": config.max_delay_seconds,
            "daily_comment_limit": config.daily_comment_limit,
            "attach_image": config.attach_image,
            "media_image_path": config.media_image_path,
            "has_openai_key": bool(config.openai_api_key and config.openai_api_key.strip()),
            "headless": config.headless
        }
    }

@app.post("/api/control/start")
def start_automation():
    if engine.state == "running":
        return {"status": "already_running"}
    engine.start()
    return {"status": "started"}

@app.post("/api/control/stop")
def stop_automation():
    engine.stop()
    return {"status": "stopped"}

@app.post("/api/browser/open-login")
def open_browser_for_login():
    """Opens browser in visible mode via independent process so the user can complete X login manually."""
    import subprocess
    import sys

    if engine.state == "running":
        engine.stop()

    login_script = os.path.join(os.path.dirname(__file__), "login.py")
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                f'start cmd /k "{sys.executable}" "{login_script}"',
                shell=True
            )
        else:
            subprocess.Popen([sys.executable, login_script])

        add_log("INFO", "Launched login helper window.")
        return {"status": "opened", "message": "Login window opened! Complete login in the browser and press Enter in the terminal."}
    except Exception as e:
        add_log("ERROR", f"Failed to launch login process: {str(e)}")
        return {"status": "error", "message": str(e)}

class ConfigUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    company_one_liner: Optional[str] = None
    company_cta: Optional[str] = None
    company_url: Optional[str] = None
    target_keywords: Optional[str] = None
    negative_keywords: Optional[str] = None
    dry_run: Optional[bool] = None
    min_delay_seconds: Optional[int] = None
    max_delay_seconds: Optional[int] = None
    daily_comment_limit: Optional[int] = None
    attach_image: Optional[bool] = None
    media_image_path: Optional[str] = None
    openai_api_key: Optional[str] = None
    headless: Optional[bool] = None

@app.post("/api/config")
def update_config(data: ConfigUpdateRequest):
    updates = data.model_dump(exclude_unset=True)
    
    # Map request keys to AppConfig fields
    field_map = {
        "target_keywords": "target_keywords_raw",
        "negative_keywords": "negative_keywords_raw",
    }
    
    for key, val in updates.items():
        attr_name = field_map.get(key, key)
        if hasattr(config, attr_name):
            setattr(config, attr_name, val)
    
    add_log("INFO", "System configuration updated from dashboard")
    return {"status": "updated", "config": config.model_dump()}

@app.get("/api/posts")
def get_posts():
    return {"posts": get_recent_posts(60)}

@app.get("/api/comments")
def get_comments():
    return {"comments": get_recent_comments(60)}

@app.get("/api/media")
def get_media_list():
    session_status = get_image_session_status()
    return {
        "media": list_available_media(),
        "media_dir": get_media_dir(),
        "today_image": get_daily_image_info(),
        "session": session_status,
        "attach_image": config.attach_image,
        "media_image_path": config.media_image_path
    }

@app.get("/api/media/session")
def get_media_session():
    return get_image_session_status()

@app.post("/api/media/rotate")
def trigger_media_rotate():
    return rotate_to_next_image()

@app.post("/api/media/upload")
async def upload_media_file(file: UploadFile = File(...)):
    media_dir = get_media_dir()
    os.makedirs(media_dir, exist_ok=True)
    file_path = os.path.join(media_dir, file.filename)
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    add_log("INFO", f"Uploaded new media image: {file.filename}")
    return {"status": "success", "filename": file.filename, "path": file_path}

@app.get("/api/logs")
def get_logs():
    return {"logs": get_recent_logs(60)}

class TestGenerateRequest(BaseModel):
    tweet_text: str
    author: Optional[str] = "crypto_user"
    keyword: Optional[str] = "crypto"

@app.post("/api/test/generate")
def test_generate_comment(req: TestGenerateRequest):
    comment = generator.generate(req.tweet_text, req.author, req.keyword)
    return {
        "reply": comment,
        "length": len(comment),
        "author": req.author,
        "mode": "ai" if (config.openai_api_key and config.openai_api_key.strip()) else "template"
    }

from template_compositor import (
    get_available_templates, load_template_layouts,
    register_new_template, render_random_template
)
from s3_fetcher import fetch_s3_market_json

@app.get("/api/templates")
def list_templates_endpoint():
    templates = get_available_templates()
    layouts = load_template_layouts()
    detailed = []
    for t in templates:
        t_path = os.path.join(TEMPLATES_DIR, t)
        meta = layouts.get(t, {})
        detailed.append({
            "filename": t,
            "size_bytes": os.path.getsize(t_path) if os.path.exists(t_path) else 0,
            "name": meta.get("name", t),
            "archetype": meta.get("archetype", "inferred"),
            "description": meta.get("description", "SpikeSignals template design"),
            "configured": t in layouts
        })
    return {
        "count": len(templates),
        "templates": detailed,
        "layouts_config": layouts
    }

@app.post("/api/templates/upload")
async def upload_template_file(file: UploadFile = File(...), archetype: Optional[str] = None):
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in valid_exts:
        raise HTTPException(status_code=400, detail="Invalid image file format. Supported: .jpg, .jpeg, .png, .webp")

    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    target_path = os.path.join(TEMPLATES_DIR, file.filename)
    with open(target_path, "wb") as f:
        content = await file.read()
        f.write(content)

    cfg = register_new_template(file.filename, {"archetype": archetype} if archetype else None)
    add_log("INFO", f"Uploaded new template '{file.filename}' (Archetype: {cfg.get('archetype', 'auto')}). Now in rotation pool.")
    return {
        "status": "success",
        "filename": file.filename,
        "path": target_path,
        "config": cfg,
        "total_templates": len(get_available_templates())
    }

class PreviewTemplateRequest(BaseModel):
    template_name: str

@app.post("/api/templates/preview")
def preview_template_endpoint(req: PreviewTemplateRequest):
    t_name = req.template_name
    t_path = os.path.join(TEMPLATES_DIR, t_name)
    if not os.path.exists(t_path):
        raise HTTPException(status_code=404, detail=f"Template {t_name} not found")

    preview_dir = os.path.join(os.path.dirname(__file__), "data", "previews")
    os.makedirs(preview_dir, exist_ok=True)
    preview_filename = f"preview_{t_name}"
    preview_path = os.path.join(preview_dir, preview_filename)

    data = fetch_s3_market_json()
    res = render_random_template(data, preview_path, template_name=t_name)
    return {
        "status": "success",
        "template": t_name,
        "preview_path": preview_path,
        "events_count": res["events_count"],
        "date": res["date"]
    }

