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
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from github import Github, GithubException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

# ========== LOGGING ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("vikram")

app = FastAPI(title="VIKRAM CMS")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── Feature routers ──────────────────────────────────────────────────────────
# Each feature is a self-contained module in features/<name>/router.py
from features.voucher.router import router as voucher_router
app.include_router(voucher_router, prefix="/voucher", tags=["voucher"])

# ========== CONFIGURATION ==========
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
SECRET_KEY     = os.environ["SECRET_KEY"]
GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
GITHUB_REPO    = os.environ["GITHUB_REPO"]
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main")
R2_ACCOUNT_ID  = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY  = os.environ["R2_ACCESS_KEY"]
R2_SECRET_KEY  = os.environ["R2_SECRET_KEY"]
R2_BUCKET_NAME = os.environ["R2_BUCKET_NAME"]
R2_PUBLIC_URL  = os.environ["R2_PUBLIC_URL"].rstrip("/")

log.info("VIKRAM CMS starting up")
log.info("GitHub repo:  %s  branch: %s", GITHUB_REPO, GITHUB_BRANCH)
log.info("R2 bucket:    %s", R2_BUCKET_NAME)
log.info("R2 public URL: %s", R2_PUBLIC_URL)

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
        "insert_marker": None,  # dynamic — see ATTRACTIONS_LOCATIONS
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

# ========== SESSION MANAGEMENT ==========
serializer = URLSafeTimedSerializer(SECRET_KEY)

def make_session_token() -> str:
    return serializer.dumps("authenticated")

def verify_session_token(token: str) -> bool:
    try:
        serializer.loads(token, max_age=60 * 60 * 24 * 7)
        return True
    except (BadSignature, SignatureExpired):
        return False

def get_token_from_request(request: Request) -> str | None:
    return request.cookies.get("vikram_session")

def require_auth(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        token = get_token_from_request(request)
        if not token or not verify_session_token(token):
            log.warning("Unauthenticated access attempt to %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper

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
    """
    Upload file bytes to Cloudflare R2 and return the absolute public URL.
    Always returns an ABSOLUTE URL (https://...) — never a relative path.
    """
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
# ROOT CAUSE FIX:
# The news2.html gallery uses item.src directly as the <img src> and <a href>.
# These resolve relative to the Cloudflare Pages domain (your website), NOT R2.
# Existing items (100.jpeg, 101.jpeg …) live in the GitHub repo so relative paths work.
# New VIKRAM uploads live ONLY in R2, so we MUST use the absolute R2 URL.
# Previously the code stripped the R2 domain, producing a relative path that 404'd.
# FIX: keep the full absolute R2 URL in both src and thumb.

def build_news_gallery_array_item(
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
) -> str:
    """
    Build a JS galleryItems entry using ABSOLUTE R2 URLs.

    New items always use batch: 1 so they appear in the first loaded set.
    The INSERT marker is at the TOP of the galleryItems array so new items
    are prepended and shown first by GalleryPaginator.

    NOTE: We use full absolute URLs (https://pub-xxx.r2.dev/...) because
    these files live in R2, not in the Cloudflare Pages repo. Relative paths
    would resolve to the Cloudflare Pages domain and return 404.
    """
    media_type = "video" if is_video else "image"

    # media_url is always an absolute R2 URL from upload_to_r2()
    # We use it directly — do NOT strip the domain prefix.
    src_url = media_url

    if is_video:
        # Derive thumbnail: swap /videos/ → /images/ and replace extension with _thumb.png
        thumb_url = src_url.replace("/videos/", "/images/")
        thumb_url = re.sub(r"\.(mp4|webm|ogg)$", "_thumb.png", thumb_url, flags=re.IGNORECASE)
    else:
        thumb_url = src_url

    item = f"    {{ type: '{media_type}', src: '{src_url}', thumb: '{thumb_url}', batch: 1 }},"
    log.info(
        "Built news gallery item  type=%s  src=%s",
        media_type, src_url,
    )
    return item

# ========== ATTRACTIONS — HTML CARD ==========
def build_attractions_html_card(
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
) -> str:
    """
    Build an HTML card matching the exact structure in attractions.html.
    Uses absolute R2 URLs — consistent with how attractions.html already works.

    Image card:
        <div class="card">
            <a href="https://..." class="popup-link" title="...">
                <img src="https://..." alt="...">
            </a>
            <div class="card-content"><h3>...</h3></div>
            <span class="card-zoom-icon"><i class="fas fa-search-plus"></i></span>
        </div>

    Video card:
        <div class="card">
            <a href="https://..." class="fancybox" data-fancybox data-type="video" title="...">
                <div class="video-thumbnail">
                    <img src="https://...PLY.webp" alt="...">
                    <div class="play-button"></div>
                </div>
            </a>
            <div class="card-content"><h3>...</h3></div>
            <span class="card-zoom-icon"><i class="fas fa-search-plus"></i></span>
        </div>
    """
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
    """
    Inject the generated markup into the HTML string at the correct marker.

    News:        inserts after  // VIKRAM:INSERT_GALLERY_ITEM  (top of galleryItems[])
    Attractions: inserts after  <!-- VIKRAM:INSERT:country:city -->
    """
    tag = build_media_markup(
        page_key, media_url, caption, is_video, content_type, original_filename
    )

    # Determine marker
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

    # Inject
    if marker in html:
        updated = html.replace(marker, marker + "\n" + tag, 1)
        log.info("Injection OK  marker found and replaced")
        return updated

    # Fallback — should never happen if markers are in place
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
    from fastapi.responses import Response
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    token = get_token_from_request(request)
    if token and verify_session_token(token):
        return RedirectResponse("/home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        log.info("Login success")
        token    = make_session_token()
        response = RedirectResponse("/home", status_code=302)
        response.set_cookie(
            key="vikram_session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )
        return response
    log.warning("Login failed — wrong password")
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Incorrect password. Please try again."},
    )

@app.get("/logout")
async def logout():
    log.info("User logged out")
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("vikram_session")
    return response

@app.get("/home", response_class=HTMLResponse)
@require_auth
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
@require_auth
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
@require_auth
async def upload(
    request:     Request,
    page_key:    str = Form(...),
    caption:     str = Form(""),
    country_key: str = Form(""),
    city_key:    str = Form(""),
    file:        UploadFile = File(...),
):
    """
    Upload a media file to R2 and inject the correct markup into the website HTML.

    Flow:
      1. Validate page / location / file type / file size
      2. Read file bytes — log size so we can confirm the file arrived
      3. Upload bytes to R2 — log the resulting public URL
      4. Fetch target HTML from GitHub
      5. Inject markup at the correct INSERT marker
      6. Commit updated HTML back to GitHub
    """
    log.info(
        "Upload request  page=%s  country=%s  city=%s  filename=%s  content_type=%s",
        page_key, country_key or "-", city_key or "-",
        file.filename, file.content_type,
    )

    # ── 1. Validate page ─────────────────────────────────────────────────────
    if page_key not in MANAGED_PAGES:
        log.error("Invalid page key: %s", page_key)
        raise HTTPException(status_code=400, detail="Invalid page.")

    # ── 2. Validate attractions location ─────────────────────────────────────
    if page_key == "attractions":
        loc_key = (country_key.lower(), city_key.lower())
        if loc_key not in ATTRACTIONS_LOCATIONS:
            log.error("Invalid attractions location: %s / %s", country_key, city_key)
            return JSONResponse(
                {"ok": False, "error": "Please select a valid country and city."},
                status_code=400,
            )

    # ── 3. Validate file type ─────────────────────────────────────────────────
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

    # ── 4. Read and validate file size ────────────────────────────────────────
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
        # ── 5. Upload to R2 ───────────────────────────────────────────────────
        media_url = upload_to_r2(
            page_key,
            file_bytes,
            file.filename or "upload.bin",
            content_type,
        )
        # media_url is always an absolute https:// URL from upload_to_r2()

        # ── 6. Fetch HTML from GitHub ─────────────────────────────────────────
        gh        = Github(GITHUB_TOKEN)
        repo      = gh.get_repo(GITHUB_REPO)
        html_path = MANAGED_PAGES[page_key]["file"]
        html_content, sha = get_github_file(repo, html_path)

        # ── 7. Inject markup ──────────────────────────────────────────────────
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

        # ── 8. Commit to GitHub ───────────────────────────────────────────────
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

@app.get("/health")
async def health():
    return {
        "status":      "ok",
        "app":         "VIKRAM CMS",
        "timestamp":   datetime.utcnow().isoformat(),
        "r2_bucket":   R2_BUCKET_NAME,
        "github_repo": GITHUB_REPO,
        "github_branch": GITHUB_BRANCH,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("ENVIRONMENT") == "development",
    )