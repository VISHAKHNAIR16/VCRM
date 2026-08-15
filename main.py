import io
import os
import re
import html as html_lib
import base64
import logging
import mimetypes
import uuid
from datetime import datetime
from functools import wraps
from urllib.parse import quote

import boto3
from botocore.config import Config
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from github import Github, GithubException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ========== LOGGING ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("vikram")

# ========== FASTAPI APP INITIALIZATION ==========
app = FastAPI(title="VIKRAM CMS")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── Feature routers ──────────────────────────────────────────────────────────
from features.voucher.router import router as voucher_router
app.include_router(voucher_router, prefix="/voucher", tags=["voucher"])

from features.quotation.router import router as quotation_router
app.include_router(quotation_router, prefix="/quotation", tags=["quotation"])

from features.vstudio.router import router as vstudio_router
app.include_router(vstudio_router, prefix="/vstudio", tags=["vstudio"])

from features.quotation_attractions.router import router as quotation_attractions_router
app.include_router(quotation_attractions_router, prefix="/quotation-attractions", tags=["quotation-attractions"])

# ========== CONFIGURATION ==========
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
STAFF_PASSWORD = os.environ.get("STAFF_PASSWORD")  # NEW: Staff password
SECRET_KEY = os.environ.get("SECRET_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

# Validate required environment variables
required_vars = [
    "ADMIN_PASSWORD", "SECRET_KEY", "GITHUB_TOKEN", "GITHUB_REPO",
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET_NAME", "R2_PUBLIC_URL"
]
missing_vars = [var for var in required_vars if not os.environ.get(var)]
if missing_vars:
    log.error("Missing required environment variables: %s", ", ".join(missing_vars))
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Staff password is optional - if not set, staff login is disabled
if not STAFF_PASSWORD:
    log.warning("STAFF_PASSWORD not set — staff login disabled")

log.info("VIKRAM CMS starting up")
log.info("GitHub repo:  %s  branch: %s", GITHUB_REPO, GITHUB_BRANCH)
log.info("R2 bucket:    %s", R2_BUCKET_NAME)
log.info("R2 public URL: %s", R2_PUBLIC_URL)

# ========== USER ROLES ==========
class UserRole:
    ADMIN = "admin"
    STAFF = "staff"

# ========== SESSION MANAGEMENT ==========
serializer = URLSafeTimedSerializer(SECRET_KEY)

def make_session_token(role: str) -> str:
    """Create a session token with role information."""
    return serializer.dumps({"authenticated": True, "role": role})

def verify_session_token(token: str) -> tuple[bool, str | None]:
    """
    Verify session token and return (is_valid, role).
    Returns (False, None) if invalid.
    """
    try:
        data = serializer.loads(token, max_age=60 * 60 * 24 * 7)
        if isinstance(data, dict) and data.get("authenticated"):
            return True, data.get("role", UserRole.STAFF)
        return False, None
    except (BadSignature, SignatureExpired):
        return False, None

def get_token_from_request(request: Request) -> str | None:
    return request.cookies.get("vikram_session")

def get_user_role(request: Request) -> str | None:
    """Get the user's role from the session token."""
    token = get_token_from_request(request)
    if not token:
        return None
    is_valid, role = verify_session_token(token)
    return role if is_valid else None

def is_admin(request: Request) -> bool:
    """Check if the current user is an admin."""
    return get_user_role(request) == UserRole.ADMIN

def is_staff(request: Request) -> bool:
    """Check if the current user is a staff member."""
    return get_user_role(request) == UserRole.STAFF

def is_authenticated(request: Request) -> bool:
    """Check if the user is authenticated (either admin or staff)."""
    token = get_token_from_request(request)
    if not token:
        return False
    is_valid, _ = verify_session_token(token)
    return is_valid

# ========== AUTH DECORATORS ==========
def require_auth(func):
    """
    Require any authenticated user (admin or staff).
    Redirects to /login if not authenticated.
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_authenticated(request):
            log.warning("Unauthenticated access attempt to %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper

def require_admin(func):
    """
    Require admin role only.
    Redirects to /home if user is not admin.
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_admin(request):
            log.warning("Non-admin access attempt to %s", request.url.path)
            return RedirectResponse("/home", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper

def require_staff(func):
    """
    Require staff role (or admin).
    Staff can access basic features, admin can access everything.
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_authenticated(request):
            log.warning("Unauthenticated access attempt to %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        # Staff and admin both can access
        return await func(request, *args, **kwargs)
    return wrapper

# ========== MANAGED PAGES ==========
MANAGED_PAGES = {
    "news": {
        "label":         "News & Gallery",
        "file":          "news2.html",
        "r2_prefix":     "media/news",
        "image_dir":     "images",
        "video_dir":     "videos",
        "insert_marker": "// VIKRAM:INSERT_GALLERY_ITEM",
    },
    "attractions": {
        "label":         "Attractions",
        "file":          "attractions.html",
        "r2_prefix":     "media/attractions",
        "image_dir":     "images",
        "video_dir":     "videos",
        "insert_marker": None,
    },
}

# ========== ATTRACTIONS LOCATION MAP ==========
ATTRACTIONS_LOCATIONS = {
    ("thailand",  "bangkok"):    "<!-- VIKRAM:INSERT:thailand:bangkok -->",
    ("thailand",  "pattaya"):    "<!-- VIKRAM:INSERT:thailand:pattaya -->",
    ("thailand",  "phuket"):     "<!-- VIKRAM:INSERT:thailand:phuket -->",
    ("thailand",  "krabi"):      "<!-- VIKRAM:INSERT:thailand:krabi -->",
    ("thailand",  "khon-kaen"):  "<!-- VIKRAM:INSERT:thailand:khon-kaen -->",
    ("thailand",  "hua-hin"):    "<!-- VIKRAM:INSERT:thailand:hua-hin -->",
    ("indonesia", "must-visit"): "<!-- VIKRAM:INSERT:indonesia:must-visit -->",
    ("vietnam",   "must-visit"): "<!-- VIKRAM:INSERT:vietnam:must-visit -->",
    ("japan",     "must-visit"): "<!-- VIKRAM:INSERT:japan:must-visit -->",
}

ATTRACTIONS_COUNTRIES = {
    "thailand": {
        "label": "Thailand",
        "cities": {
            "bangkok":   "Bangkok",
            "pattaya":   "Pattaya",
            "phuket":    "Phuket",
            "krabi":     "Krabi",
            "khon-kaen": "Khon Kaen",
            "hua-hin":   "Hua Hin",
        },
    },
    "indonesia": {"label": "Indonesia", "cities": {"must-visit": "Must Visit"}},
    "vietnam":   {"label": "Vietnam",   "cities": {"must-visit": "Must Visit"}},
    "japan":     {"label": "Japan",     "cities": {"must-visit": "Must Visit"}},
}

# ========== SESSION MANAGEMENT (Legacy) ==========
# Keep legacy functions for backward compatibility
def make_session_token_legacy() -> str:
    return serializer.dumps("authenticated")

def verify_session_token_legacy(token: str) -> bool:
    try:
        serializer.loads(token, max_age=60 * 60 * 24 * 7)
        return True
    except (BadSignature, SignatureExpired):
        return False

# ========== R2 CLOUDFLARE STORAGE ==========
def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

def build_r2_key(page_key: str, filename: str, content_type: str) -> str:
    page_config  = MANAGED_PAGES[page_key]
    ext          = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    safe_stem    = re.sub(r"[^a-zA-Z0-9_-]+", "-", os.path.splitext(filename)[0])
    safe_stem    = safe_stem.strip("-").lower() or "file"
    is_video     = content_type.startswith("video/")
    media_folder = page_config["video_dir"] if is_video else page_config["image_dir"]
    timestamp    = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    unique_id    = uuid.uuid4().hex[:12]
    unique_name  = f"{timestamp}-{unique_id}-{safe_stem}.{ext}"
    key          = f"{page_config['r2_prefix']}/{media_folder}/{unique_name}"
    log.debug("Built R2 key: %s", key)
    return key

def upload_to_r2(page_key: str, file_bytes: bytes, filename: str, content_type: str) -> str:
    if not file_bytes:
        raise ValueError("upload_to_r2 received empty file_bytes — nothing to upload")

    client     = get_r2_client()
    object_key = build_r2_key(page_key, filename, content_type)
    size_kb    = len(file_bytes) / 1024

    log.info(
        "R2 upload START  page=%s  key=%s  size=%.1f KB  type=%s",
        page_key, object_key, size_kb, content_type,
    )

    client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=object_key,
        Body=file_bytes,
        ContentType=content_type,
    )

    encoded_key = "/".join(quote(part) for part in object_key.split("/"))
    public_url  = f"{R2_PUBLIC_URL}/{encoded_key}"

    log.info("R2 upload OK     url=%s", public_url)
    return public_url

# ========== GITHUB OPERATIONS ==========
def get_github_file(repo, path: str):
    log.info("GitHub GET  repo=%s  path=%s  branch=%s", GITHUB_REPO, path, GITHUB_BRANCH)
    file_obj = repo.get_contents(path, ref=GITHUB_BRANCH)
    content  = base64.b64decode(file_obj.content).decode("utf-8")
    log.info("GitHub GET OK  size=%d chars  sha=%s", len(content), file_obj.sha[:8])
    return content, file_obj.sha

def update_github_file(repo, path: str, sha: str, content: str, commit_msg: str):
    log.info("GitHub COMMIT  path=%s  msg=%r", path, commit_msg)
    repo.update_file(
        path=path,
        message=commit_msg,
        content=content,
        sha=sha,
        branch=GITHUB_BRANCH,
    )
    log.info("GitHub COMMIT OK")

# ========== NEWS — GALLERY ARRAY ITEM ==========
def build_news_gallery_array_item(
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
) -> str:
    media_type = "video" if is_video else "image"
    src_url = media_url

    if is_video:
        thumb_url = src_url.replace("/videos/", "/images/")
        thumb_url = re.sub(r"\.(mp4|webm|ogg)$", "_thumb.png", thumb_url, flags=re.IGNORECASE)
    else:
        thumb_url = src_url

    item = f"    {{ type: '{media_type}', src: '{src_url}', thumb: '{thumb_url}', batch: 1 }},"
    log.info("Built news gallery item  type=%s  src=%s", media_type, src_url)
    return item

# ========== ATTRACTIONS — HTML CARD ==========
def build_attractions_html_card(
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
) -> str:
    safe_caption = html_lib.escape(caption or "VayoAura attraction")
    safe_alt     = html_lib.escape(caption or "VayoAura attraction media", quote=True)
    safe_url     = html_lib.escape(media_url, quote=True)

    if is_video:
        thumb_url = re.sub(r"\.(mp4|webm|ogg)$", "PLY.webp", safe_url, flags=re.IGNORECASE)
        inner = (
            f'<a href="{safe_url}" class="fancybox" data-fancybox data-type="video" title="{safe_caption}">\n'
            f'                    <div class="video-thumbnail">\n'
            f'                        <img src="{thumb_url}" alt="{safe_alt}">\n'
            f'                        <div class="play-button"></div>\n'
            f'                    </div>\n'
            f'                </a>'
        )
    else:
        inner = (
            f'<a href="{safe_url}" class="popup-link" title="{safe_caption}">\n'
            f'                    <img src="{safe_url}" alt="{safe_alt}">\n'
            f'                </a>'
        )

    card = (
        f'\n                <div class="card">\n'
        f'                {inner}\n'
        f'                    <div class="card-content">\n'
        f'                        <h3>{safe_caption}</h3>\n'
        f'                    </div>\n'
        f'                    <span class="card-zoom-icon"><i class="fas fa-search-plus"></i></span>\n'
        f'                </div>'
    )
    log.info("Built attractions card  video=%s  url=%s", is_video, media_url)
    return card

# ========== DISPATCHER ==========
def build_media_markup(
    page_key: str,
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
) -> str:
    if page_key == "news":
        return build_news_gallery_array_item(
            media_url, caption, is_video, content_type, original_filename
        )
    elif page_key == "attractions":
        return build_attractions_html_card(
            media_url, caption, is_video, content_type, original_filename
        )
    else:
        raise ValueError(f"Unsupported page key: {page_key}")

# ========== HTML INJECTION ==========
def inject_media_into_html(
    html: str,
    page_key: str,
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
    country_key: str = "",
    city_key: str = "",
) -> str:
    tag = build_media_markup(
        page_key, media_url, caption, is_video, content_type, original_filename
    )

    if page_key == "news":
        marker = MANAGED_PAGES["news"]["insert_marker"]
        log.info("Injection target: news marker=%r", marker)
    elif page_key == "attractions":
        loc_key = (country_key.lower(), city_key.lower())
        marker  = ATTRACTIONS_LOCATIONS.get(loc_key)
        if not marker:
            raise ValueError(
                f"Unknown attractions location: country='{country_key}' city='{city_key}'. "
                f"Valid: {sorted(ATTRACTIONS_LOCATIONS.keys())}"
            )
        log.info("Injection target: attractions marker=%r", marker)
    else:
        raise ValueError(f"Unsupported page key: {page_key}")

    if marker in html:
        updated = html.replace(marker, marker + "\n" + tag, 1)
        log.info("Injection OK  marker found and replaced")
        return updated

    log.warning("Marker %r NOT FOUND in HTML — falling back to </body> insertion", marker)
    if "</body>" in html:
        return html.replace("</body>", tag + "\n</body>", 1)
    return html + tag

# ========== ROUTES ==========

@app.get("/favicon.ico")
async def favicon():
    favicon_file = os.path.join("static", "favicon.ico")
    if os.path.exists(favicon_file):
        return FileResponse(favicon_file, media_type="image/x-icon")
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    token = get_token_from_request(request)
    if token:
        is_valid, _ = verify_session_token(token)
        if is_valid:
            return RedirectResponse("/home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    # Check if password matches admin or staff
    role = None
    
    if password == ADMIN_PASSWORD:
        role = UserRole.ADMIN
        log.info("Admin login success")
    elif STAFF_PASSWORD and password == STAFF_PASSWORD:
        role = UserRole.STAFF
        log.info("Staff login success")
    else:
        log.warning("Login failed — wrong password")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Incorrect password. Please try again."},
        )
    
    token = make_session_token(role)
    response = RedirectResponse("/home", status_code=302)
    response.set_cookie(
        key="vikram_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response

@app.get("/logout")
async def logout():
    log.info("User logged out")
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("vikram_session")
    return response

@app.get("/home", response_class=HTMLResponse)
@require_auth
async def home(request: Request):
    """Home page with role-based feature visibility."""
    role = get_user_role(request)
    is_admin_user = (role == UserRole.ADMIN)
    
    # Features available to all authenticated users
    all_features = [
        {
            "id": "quotation",
            "title": "Quotation Tool",
            "icon": "💰",
            "description": "Search services and build quotes with commission and VAT.",
            "url": "/quotation",
            "badge": "Live" if is_admin_user else "Staff Access",
            "badge_class": "badge-live" if is_admin_user else "badge-staff",
        },
        {
            "id": "voucher",
            "title": "VTOP WEB",
            "icon": "📄",
            "description": "Generate hotel and tour booking confirmation PDFs instantly.",
            "url": "/voucher",
            "badge": "Live" if is_admin_user else "Staff Access",
            "badge_class": "badge-live" if is_admin_user else "badge-staff",
        },
    ]
    
    # Admin-only features
    admin_features = [
        {
            "id": "dashboard",
            "title": "Content Manager",
            "icon": "📤",
            "description": "Upload images and videos to your website. Files go to Cloudflare R2.",
            "url": "/dashboard",
            "badge": "Admin Only",
            "badge_class": "badge-admin",
        },
        {
            "id": "vstudio",
            "title": "VStudio",
            "icon": "🎨",
            "description": "Create stunning social media content for your brand.",
            "url": "/vstudio",
            "badge": "Admin Only",
            "badge_class": "badge-admin",
        },
    ]
    
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "features": all_features,
            "admin_features": admin_features if is_admin_user else [],
            "is_admin": is_admin_user,
            "role": role,
        }
    )

@app.get("/dashboard", response_class=HTMLResponse)
@require_admin  # Only admin can access
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request":               request,
            "pages":                 MANAGED_PAGES,
            "attractions_countries": ATTRACTIONS_COUNTRIES,
        },
    )

@app.post("/upload")
@require_admin  # Only admin can upload
async def upload(
    request:     Request,
    page_key:    str = Form(...),
    caption:     str = Form(""),
    country_key: str = Form(""),
    city_key:    str = Form(""),
    file:        UploadFile = File(...),
):
    log.info(
        "Upload request  page=%s  country=%s  city=%s  filename=%s  content_type=%s",
        page_key, country_key or "-", city_key or "-",
        file.filename, file.content_type,
    )

    if page_key not in MANAGED_PAGES:
        log.error("Invalid page key: %s", page_key)
        raise HTTPException(status_code=400, detail="Invalid page.")

    if page_key == "attractions":
        loc_key = (country_key.lower(), city_key.lower())
        if loc_key not in ATTRACTIONS_LOCATIONS:
            log.error("Invalid attractions location: %s / %s", country_key, city_key)
            return JSONResponse(
                {"ok": False, "error": "Please select a valid country and city."},
                status_code=400,
            )

    allowed_images = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
    allowed_videos = {"video/mp4", "video/webm", "video/ogg"}

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/octet-stream"
    )
    is_video = content_type in allowed_videos

    if content_type not in allowed_images and content_type not in allowed_videos:
        log.warning("Rejected file type: %s", content_type)
        return JSONResponse(
            {
                "ok":    False,
                "error": f"Unsupported file type: {content_type}. "
                         "Use JPG, PNG, WebP, GIF for images; MP4, WebM, OGG for videos.",
            },
            status_code=400,
        )

    file_bytes = await file.read()
    size_bytes = len(file_bytes)
    size_mb    = size_bytes / (1024 * 1024)

    log.info(
        "File received  name=%s  size=%.2f MB  type=%s  is_video=%s",
        file.filename, size_mb, content_type, is_video,
    )

    if size_bytes == 0:
        log.error("Empty file received — aborting")
        return JSONResponse(
            {"ok": False, "error": "The file appears to be empty. Please try again."},
            status_code=400,
        )

    if size_bytes > 50 * 1024 * 1024:
        log.warning("File too large: %.2f MB", size_mb)
        return JSONResponse(
            {"ok": False, "error": f"File too large: {size_mb:.1f} MB. Maximum is 50 MB."},
            status_code=400,
        )

    try:
        media_url = upload_to_r2(
            page_key,
            file_bytes,
            file.filename or "upload.bin",
            content_type,
        )

        gh        = Github(GITHUB_TOKEN)
        repo      = gh.get_repo(GITHUB_REPO)
        html_path = MANAGED_PAGES[page_key]["file"]
        html_content, sha = get_github_file(repo, html_path)

        updated_html = inject_media_into_html(
            html=html_content,
            page_key=page_key,
            media_url=media_url,
            caption=caption,
            is_video=is_video,
            content_type=content_type,
            original_filename=file.filename or "upload.bin",
            country_key=country_key,
            city_key=city_key,
        )

        media_type_str = "video" if is_video else "image"
        location_str   = f"{country_key}/{city_key}" if page_key == "attractions" else html_path
        commit_msg = (
            f"VIKRAM: Add {media_type_str} '{caption or 'Untitled'}' "
            f"to {location_str} [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
        )
        update_github_file(repo, html_path, sha, updated_html, commit_msg)

        log.info(
            "Upload complete  page=%s  location=%s  media_type=%s  url=%s",
            page_key, location_str, media_type_str, media_url,
        )

        return JSONResponse({
            "ok":         True,
            "media_url":  media_url,
            "page":       MANAGED_PAGES[page_key]["label"],
            "page_key":   page_key,
            "country":    country_key,
            "city":       city_key,
            "r2_bucket":  R2_BUCKET_NAME,
            "r2_prefix":  MANAGED_PAGES[page_key]["r2_prefix"],
            "media_type": media_type_str,
            "caption":    caption,
            "filename":   file.filename,
            "size_bytes": size_bytes,
        })

    except GithubException as exc:
        error_message = getattr(exc, "data", {}).get("message", str(exc))
        log.error("GitHub error: %s", error_message)
        return JSONResponse(
            {"ok": False, "error": f"GitHub error: {error_message}"},
            status_code=500,
        )
    except Exception as exc:
        log.exception("Unexpected error during upload: %s", exc)
        return JSONResponse(
            {"ok": False, "error": f"Upload failed: {str(exc)}"},
            status_code=500,
        )

# ========== PDF GENERATION ENDPOINTS ==========
from features.voucher.generator import (
    warmup_caches, 
    generate_hotel_pdf, 
    generate_tour_pdf,
    generate_hotel_pdf_async,
    generate_tour_pdf_async,
    get_cache_stats
)

@app.on_event("startup")
async def startup_event():
    log.info("Starting application and warming up caches...")

    try:
        from features.quotation.build_db import DB_PATH as _QDB, main as _build_qdb
        if not _QDB.exists():
            log.warning("quotation.db missing at %s — rebuilding from Excel (~10 s)", _QDB)
            _build_qdb(dry_run=False)
            log.info("quotation.db rebuilt OK")
        else:
            log.info("quotation.db found at %s", _QDB)
    except Exception as _e:
        log.error("quotation.db check failed: %s", _e)

    try:
        warmup_caches()
        stats = get_cache_stats()
        log.info("Cache warmup complete: %s", stats)
    except Exception as e:
        log.warning("Cache warmup failed (will load on first request): %s", e)

@app.get("/voucher/generate-hotel-pdf", response_class=HTMLResponse)
@require_staff  # Staff can access
async def hotel_voucher_form(request: Request):
    return templates.TemplateResponse("hotel_voucher_form.html", {"request": request})

@app.get("/voucher/generate-tour-pdf", response_class=HTMLResponse)
@require_staff  # Staff can access
async def tour_voucher_form(request: Request):
    return templates.TemplateResponse("tour_voucher_form.html", {"request": request})

@app.post("/voucher/generate-hotel-pdf")
@require_staff
async def generate_hotel_pdf_endpoint(
    request: Request,
    booking_number: str = Form(...),
    hotel_cfn: str = Form(...),
    guest_name: str = Form(...),
    country: str = Form(...),
    hotel_name: str = Form(...),
    address: str = Form(...),
    contact_number: str = Form(...),
    cancellation_policy: str = Form(...),
    check_in: str = Form(...),
    check_out: str = Form(...),
    book_payable_by: str = Form(...),
    remarks: str = Form(""),
    num_rooms: str = Form("1"),
    extra_beds: str = Form("0"),
    num_adults: str = Form("1"),
    num_children: str = Form("0"),
    room_type: str = Form(...),
):
    try:
        form_data = {
            "booking_number": booking_number,
            "hotel_cfn": hotel_cfn,
            "guest_name": guest_name,
            "country": country,
            "hotel_name": hotel_name,
            "address": address,
            "contact_number": contact_number,
            "cancellation_policy": cancellation_policy,
            "check_in": check_in,
            "check_out": check_out,
            "book_payable_by": book_payable_by,
            "remarks": remarks,
            "num_rooms": num_rooms,
            "extra_beds": extra_beds,
            "num_adults": num_adults,
            "num_children": num_children,
            "room_type": room_type,
        }
        
        pdf_bytes, filename = await generate_hotel_pdf_async(form_data)
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        log.exception("Failed to generate hotel PDF: %s", e)
        return JSONResponse(
            {"ok": False, "error": f"PDF generation failed: {str(e)}"},
            status_code=500
        )

@app.post("/voucher/generate-tour-pdf")
@require_staff
async def generate_tour_pdf_endpoint(
    request: Request,
    booking_number: str = Form(...),
    guest_name: str = Form(...),
    guest_mobile_no: str = Form(...),
    tour_name: str = Form(...),
    package_name: str = Form(...),
    service_date: str = Form(...),
    pickup_from: str = Form(...),
    drop_to: str = Form(...),
    pick_time: str = Form(...),
    cancellation_policy: str = Form(...),
    book_payable_by: str = Form(...),
    num_adults: str = Form("1"),
    num_children: str = Form("0"),
    service_type: str = Form(...),
):
    try:
        form_data = {
            "booking_number": booking_number,
            "guest_name": guest_name,
            "guest_mobile_no": guest_mobile_no,
            "tour_name": tour_name,
            "package_name": package_name,
            "service_date": service_date,
            "pickup_from": pickup_from,
            "drop_to": drop_to,
            "pick_time": pick_time,
            "cancellation_policy": cancellation_policy,
            "book_payable_by": book_payable_by,
            "num_adults": num_adults,
            "num_children": num_children,
            "service_type": service_type,
        }
        
        pdf_bytes, filename = await generate_tour_pdf_async(form_data)
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        log.exception("Failed to generate tour PDF: %s", e)
        return JSONResponse(
            {"ok": False, "error": f"PDF generation failed: {str(e)}"},
            status_code=500
        )

@app.get("/voucher/cache-stats")
@require_admin  # Only admin can see cache stats
async def voucher_cache_stats():
    return JSONResponse(get_cache_stats())

@app.get("/health")
async def health():
    return {
        "status":      "ok",
        "app":         "VIKRAM CMS",
        "version":     "4.0.0",
        "timestamp":   datetime.utcnow().isoformat(),
        "r2_bucket":   R2_BUCKET_NAME,
        "github_repo": GITHUB_REPO,
        "github_branch": GITHUB_BRANCH,
        "voucher_cache": get_cache_stats(),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("ENVIRONMENT") == "development",
    )