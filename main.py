import os
import re
import html as html_lib
import base64
import mimetypes
import uuid
from datetime import datetime
from functools import wraps
from urllib.parse import quote

import boto3
from botocore.config import Config
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from github import Github, GithubException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="VIKRAM CMS")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ========== CONFIGURATION ==========
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
SECRET_KEY = os.environ["SECRET_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY = os.environ["R2_ACCESS_KEY"]
R2_SECRET_KEY = os.environ["R2_SECRET_KEY"]
R2_BUCKET_NAME = os.environ["R2_BUCKET_NAME"]
R2_PUBLIC_URL = os.environ["R2_PUBLIC_URL"].rstrip("/")
FAVICON_PATH = "static/favicon.ico"
FAVICON_TYPE = "image/x-icon"

DEFAULT_INSERT_MARKER = "<!-- VIKRAM:INSERT -->"

# ========== MANAGED PAGES CONFIGURATION ==========
MANAGED_PAGES = {
    "news": {
        "label": "News & Gallery",
        "file": "news2.html",
        "r2_prefix": "media/news",
        "image_dir": "images",
        "video_dir": "videos",
        "insert_marker": "// VIKRAM:INSERT_GALLERY_ITEM",
    },
    "attractions": {
        "label": "Attractions",
        "file": "attractions.html",
        "r2_prefix": "media/attractions",
        "image_dir": "images",
        "video_dir": "videos",
        "insert_marker": "<!-- VIKRAM:INSERT -->",
    },
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
    """
    Build the R2 object key based on page configuration and content type.
    
    Structure:
    - media/attractions/images/20250101-120000-abc123def456-mountain.jpg
    - media/attractions/videos/20250101-120000-abc123def456-waterfall.mp4
    - media/news/images/20250101-120000-abc123def456-event.jpg
    - media/news/videos/20250101-120000-abc123def456-event.mp4
    """
    page_config = MANAGED_PAGES[page_key]
    
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    else:
        ext = "bin"
    
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", os.path.splitext(filename)[0])
    safe_stem = safe_stem.strip("-").lower() or "file"
    
    is_video = content_type.startswith("video/")
    media_folder = page_config["video_dir"] if is_video else page_config["image_dir"]
    
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    unique_id = uuid.uuid4().hex[:12]
    unique_name = f"{timestamp}-{unique_id}-{safe_stem}.{ext}"
    
    return f"{page_config['r2_prefix']}/{media_folder}/{unique_name}"


def upload_to_r2(page_key: str, file_bytes: bytes, filename: str, content_type: str) -> str:
    """Upload file to R2 and return the public URL."""
    client = get_r2_client()
    object_key = build_r2_key(page_key, filename, content_type)
    
    client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=object_key,
        Body=file_bytes,
        ContentType=content_type,
    )
    
    encoded_key = "/".join(quote(part) for part in object_key.split("/"))
    return f"{R2_PUBLIC_URL}/{encoded_key}"


# ========== GITHUB OPERATIONS ==========
def get_github_file(repo, path: str):
    file_obj = repo.get_contents(path, ref=GITHUB_BRANCH)
    content = base64.b64decode(file_obj.content).decode("utf-8")
    return content, file_obj.sha


def update_github_file(repo, path: str, sha: str, content: str, commit_msg: str):
    repo.update_file(
        path=path,
        message=commit_msg,
        content=content,
        sha=sha,
        branch=GITHUB_BRANCH,
    )


# ========== FAVICON MANAGEMENT ==========
def ensure_favicon(html: str) -> str:
    """Ensure favicon is present in HTML."""
    favicon_pattern = r'<link[^>]+rel=["\'](?:shortcut\s+icon|icon)["\'][^>]*>'
    if re.search(favicon_pattern, html, re.IGNORECASE):
        return html
    
    favicon_tag = f'    <link rel="icon" href="{html_lib.escape(FAVICON_PATH, quote=True)}" type="{FAVICON_TYPE}">\n'
    
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n{favicon_tag}", 1)
    elif "<HEAD>" in html:
        return html.replace("<HEAD>", f"<HEAD>\n{favicon_tag}", 1)
    else:
        return favicon_tag + html


# ========== NEWS PAGE - GALLERY ARRAY ITEM GENERATOR ==========
def build_news_gallery_array_item(
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str
) -> str:
    """
    Build JavaScript array entry for news page galleryItems.
    
    Format:
    { type: 'image', src: 'media/news/images/159.jpg', thumb: 'media/news/images/159.jpg', batch: 10 },
    { type: 'video', src: 'media/news/videos/EVENT6.mp4', thumb: 'media/news/images/EVENT6_thumb.png', batch: 11 },
    """
    safe_url = media_url
    media_type = "video" if is_video else "image"
    
    # Extract the relative path from the full URL
    # Full URL: https://your-domain.com/media/news/images/filename.jpg
    # We need: media/news/images/filename.jpg
    if safe_url.startswith(R2_PUBLIC_URL):
        relative_path = safe_url[len(R2_PUBLIC_URL) + 1:]  # +1 for the slash
    else:
        relative_path = safe_url
    
    # For thumbnails:
    # - Images: same as src
    # - Videos: replace extension with _thumb.png in the images folder
    if is_video:
        # Change videos/filename.mp4 to images/filename_thumb.png
        thumb_path = relative_path.replace("/videos/", "/images/")
        thumb_path = re.sub(r'\.(mp4|webm|ogg)$', '_thumb.png', thumb_path)
    else:
        thumb_path = relative_path
    
    # Determine batch number (we'll use a placeholder that gets updated by the system)
    # The batch can be determined by the current gallery length or set to a default
    batch_placeholder = "{{BATCH}}"
    
    # Build the JavaScript object string
    return f"    {{ type: '{media_type}', src: '{relative_path}', thumb: '{thumb_path}', batch: {batch_placeholder} }},"


# ========== ATTRACTIONS PAGE - HTML CARD GENERATOR ==========
def build_attractions_html_card(
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str
) -> str:
    """
    Build HTML markup for attractions page media items.
    Matches the exact card structure used in attractions.html.

    Image card structure:
        <div class="card">
            <a href="..." class="popup-link" title="...">
                <img src="..." alt="...">
            </a>
            <div class="card-content">
                <h3>...</h3>
            </div>
            <span class="card-zoom-icon"><i class="fas fa-search-plus"></i></span>
        </div>

    Video card structure — same wrapper, fancybox link with poster thumbnail:
        <div class="card">
            <a href="..." class="popup-link" data-fancybox data-type="video" title="...">
                <img src="...PLY.webp" alt="...">
            </a>
            <div class="card-content">
                <h3>...</h3>
            </div>
            <span class="card-zoom-icon"><i class="fas fa-search-plus"></i></span>
        </div>
    """
    safe_caption = html_lib.escape(caption or "VayoAura attraction")
    safe_alt = html_lib.escape(caption or "VayoAura attraction media", quote=True)
    safe_url = html_lib.escape(media_url, quote=True)

    if is_video:
        # Derive poster thumbnail: replace video extension with PLY.webp
        thumb_url = re.sub(r'\.(mp4|webm|ogg)$', 'PLY.webp', safe_url, flags=re.IGNORECASE)
        media_content = (
            f'<a href="{safe_url}" class="popup-link" data-fancybox data-type="video" title="{safe_caption}">\n'
            f'                        <img src="{thumb_url}" alt="{safe_alt}">\n'
            f'                    </a>'
        )
    else:
        media_content = (
            f'<a href="{safe_url}" class="popup-link" title="{safe_caption}">\n'
            f'                        <img src="{safe_url}" alt="{safe_alt}">\n'
            f'                    </a>'
        )

    return (
        f'\n                <div class="card">\n'
        f'                    {media_content}\n'
        f'                    <div class="card-content">\n'
        f'                        <h3>{safe_caption}</h3>\n'
        f'                    </div>\n'
        f'                    <span class="card-zoom-icon"><i class="fas fa-search-plus"></i></span>\n'
        f'                </div>'
    )


# ========== MEDIA MARKUP DISPATCHER ==========
def build_media_markup(
    page_key: str,
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str
) -> str:
    """Dispatch to appropriate builder based on page key."""
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


# ========== FIND NEXT BATCH NUMBER FOR NEWS ==========
def find_next_batch_number(html_content: str) -> int:
    """
    Find the highest batch number in the galleryItems array and return next.
    If no batch numbers found, return 13 (default for new items).
    """
    # Pattern to match batch numbers: batch: 10, batch: 11, etc.
    pattern = r'batch:\s*(\d+)'
    matches = re.findall(pattern, html_content)
    
    if matches:
        # Convert to integers and find max
        batch_numbers = [int(m) for m in matches]
        max_batch = max(batch_numbers)
        return max_batch + 1
    
    # Default starting batch for new items
    return 13


# ========== HTML INJECTION ==========
def inject_media_into_html(
    html: str,
    page_key: str,
    media_url: str,
    caption: str,
    is_video: bool,
    content_type: str,
    original_filename: str,
) -> str:
    """Inject media markup into HTML at the appropriate location."""
    # Generate media markup
    tag = build_media_markup(
        page_key, media_url, caption, is_video, content_type, original_filename
    )
    
    page_config = MANAGED_PAGES[page_key]
    marker = page_config.get("insert_marker", DEFAULT_INSERT_MARKER)
    
    # For news page, replace batch placeholder with actual batch number
    if page_key == "news":
        next_batch = find_next_batch_number(html)
        tag = tag.replace("{{BATCH}}", str(next_batch))
    
    # Try primary insertion marker
    if marker in html:
        return html.replace(marker, marker + "\n" + tag, 1)
    
    # Fallback for news: try to find the galleryItems array
    if page_key == "news":
        # Look for the end of the galleryItems array
        gallery_array_pattern = r'(const galleryItems = \[.*?)(\s*\];)'
        match = re.search(gallery_array_pattern, html, re.DOTALL)
        
        if match:
            # Insert before the closing bracket
            insertion_point = match.end(1)
            new_html = html[:insertion_point] + "\n" + tag + html[insertion_point:]
            return new_html
        
        # Alternative: look for "// Event videos" comment
        event_videos_pattern = r'(// Event videos.*?\n)'
        match = re.search(event_videos_pattern, html)
        if match:
            insertion_point = match.end()
            new_html = html[:insertion_point] + tag + "\n" + html[insertion_point:]
            return new_html
    
    # Last resort: insert before closing body tag
    if "</body>" in html:
        return html.replace("</body>", tag + "\n</body>", 1)
    
    return html + tag


# ========== ROUTES ==========
@app.get("/favicon.ico")
async def favicon():
    """Serve favicon for the VIKRAM app UI only — never injected into website HTML."""
    from fastapi.responses import FileResponse
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
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None}
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        token = make_session_token()
        response = RedirectResponse("/dashboard", status_code=302)
        response.set_cookie(
            key="vikram_session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )
        return response
    
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Incorrect password. Please try again."},
    )


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("vikram_session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
@require_auth
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "pages": MANAGED_PAGES}
    )


@app.post("/upload")
@require_auth
async def upload(
    request: Request,
    page_key: str = Form(...),
    caption: str = Form(""),
    file: UploadFile = File(...),
):
    """Upload media file to R2 and inject into the selected HTML page."""
    if page_key not in MANAGED_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid page selected. Choose from: {', '.join(MANAGED_PAGES.keys())}"
        )
    
    allowed_images = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
    allowed_videos = {"video/mp4", "video/webm", "video/ogg"}
    
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    is_video = content_type in allowed_videos
    
    if content_type not in allowed_images and content_type not in allowed_videos:
        return JSONResponse(
            {
                "ok": False,
                "error": f"Unsupported file type: {content_type}. "
                         f"Use JPG, PNG, WebP, GIF for images; MP4, WebM, OGG for videos."
            },
            status_code=400,
        )
    
    file_bytes = await file.read()
    max_size = 50 * 1024 * 1024  # 50 MB
    if len(file_bytes) > max_size:
        return JSONResponse(
            {
                "ok": False,
                "error": f"File too large: {len(file_bytes) / (1024*1024):.1f} MB. "
                         f"Maximum size is 50 MB."
            },
            status_code=400,
        )
    
    try:
        # Upload to R2
        media_url = upload_to_r2(
            page_key,
            file_bytes,
            file.filename or "upload.bin",
            content_type
        )
        
        # Connect to GitHub
        gh = Github(GITHUB_TOKEN)
        repo = gh.get_repo(GITHUB_REPO)
        
        # Get current HTML file
        html_path = MANAGED_PAGES[page_key]["file"]
        html_content, sha = get_github_file(repo, html_path)
        
        # Inject media markup
        updated_html = inject_media_into_html(
            html=html_content,
            page_key=page_key,
            media_url=media_url,
            caption=caption,
            is_video=is_video,
            content_type=content_type,
            original_filename=file.filename or "upload.bin",
        )
        
        # Commit to GitHub
        media_type_str = "video" if is_video else "image"
        commit_msg = (
            f"VIKRAM: Add {media_type_str} '{caption or 'Untitled'}' to {html_path} "
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
        )
        update_github_file(repo, html_path, sha, updated_html, commit_msg)
        
        return JSONResponse({
            "ok": True,
            "media_url": media_url,
            "page": MANAGED_PAGES[page_key]["label"],
            "page_key": page_key,
            "r2_bucket": R2_BUCKET_NAME,
            "r2_prefix": MANAGED_PAGES[page_key]["r2_prefix"],
            "media_type": media_type_str,
            "caption": caption,
            "filename": file.filename,
            "size_bytes": len(file_bytes),
        })
        
    except GithubException as exc:
        error_message = getattr(exc, "data", {}).get("message", str(exc))
        return JSONResponse(
            {"ok": False, "error": f"GitHub error: {error_message}"},
            status_code=500
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"Upload failed: {str(exc)}"},
            status_code=500
        )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": "VIKRAM CMS",
        "timestamp": datetime.utcnow().isoformat(),
        "r2_bucket": R2_BUCKET_NAME,
        "github_repo": GITHUB_REPO,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("ENVIRONMENT") == "development"
    )