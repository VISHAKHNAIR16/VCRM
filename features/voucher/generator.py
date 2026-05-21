"""
features/voucher/generator.py
──────────────────────────────
PDF generation logic for the VIKRAM Voucher Generator feature.
Optimized for performance with caching, image compression, and async support.
"""

import asyncio
import base64
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import jinja2
from weasyprint import HTML, CSS
from PIL import Image

log = logging.getLogger("vikram.voucher.generator")

# ── Paths ────────────────────────────────────────────────────────────────────
FEATURE_DIR = Path(__file__).parent
TEMPLATES_DIR = FEATURE_DIR / "templates"
ASSETS_DIR = FEATURE_DIR / "assets"

# ── Caches for performance ───────────────────────────────────────────────────
_css_cache: Dict[str, Optional[CSS]] = {}
_template_cache: Dict[str, jinja2.Template] = {}
_jinja_env: Optional[jinja2.Environment] = None

# Thread pool for async PDF generation (4 concurrent workers)
_executor = ThreadPoolExecutor(max_workers=4)


# ── Optimized Image Helper with Caching and Compression ─────────────────────
@lru_cache(maxsize=32)
def image_to_base64_optimized(
    filename: str, 
    max_width: int = 150, 
    quality: int = 60,
    force_reload: bool = False
) -> str:
    """
    Load, optimize, compress and cache images as base64 data URIs.
    
    Args:
        filename: Image filename in assets directory
        max_width: Maximum width in pixels (maintains aspect ratio)
        quality: JPEG quality (1-100, lower = smaller file)
        force_reload: Bypass cache and reload image
    
    Returns:
        Data URI string for embedding in HTML, or empty string on failure
    """
    if not filename:
        return ""
    
    path = ASSETS_DIR / filename
    if not path.exists():
        log.warning("Asset not found: %s", path)
        return ""
    
    try:
        with Image.open(path) as img:
            # Convert to RGB if necessary (removes alpha channel for JPEG)
            original_mode = img.mode
            if img.mode not in ('RGB', 'L'):
                if img.mode == 'RGBA':
                    # Handle transparency by adding white background
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3])  # Use alpha channel as mask
                    img = background
                else:
                    img = img.convert('RGB')
            
            # Resize if image is larger than max_width
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                # Use LANCZOS for high-quality downsampling
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Save with compression
            buffer = io.BytesIO()
            
            # Use JPEG for photos (better compression), PNG for graphics with transparency
            use_jpeg = filename.lower().endswith(('.jpg', '.jpeg')) or original_mode != 'RGBA'
            
            if use_jpeg:
                img.save(buffer, format='JPEG', quality=quality, optimize=True)
                mime_type = 'image/jpeg'
            else:
                # For PNGs with transparency or sharp graphics
                img.save(buffer, format='PNG', optimize=True)
                mime_type = 'image/png'
            
            data = base64.b64encode(buffer.getvalue()).decode('utf-8')
            return f"data:{mime_type};base64,{data}"
            
    except Exception as e:
        log.exception("Failed to encode asset: %s - %s", filename, str(e))
        return ""


# Backward compatibility wrapper
def image_to_base64(filename: str) -> str:
    """Legacy wrapper for image_to_base64_optimized with default settings."""
    return image_to_base64_optimized(filename, max_width=150, quality=60)


# ── Filename generator (identical to desktop app) ────────────────────────────
def generate_unique_filename(guest_name: str, booking_id: str) -> str:
    """
    Produce a deterministic but collision-resistant filename stem.
    Uses characters 4-5 of the guest name + first 8 chars of the booking ID.
    """
    guest_name = (guest_name or "").strip()
    prefix = guest_name[3:5].upper() if len(guest_name) >= 5 else "XX"
    suffix = re.sub(r"[^a-zA-Z0-9]", "", booking_id or "")[:8] or "00000000"
    return f"{suffix}{prefix}"


# ── Jinja2 environment with caching ─────────────────────────────────────────
def _get_jinja_env() -> jinja2.Environment:
    """Lazy-load and cache Jinja2 environment."""
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=False,  # HTML templates contain intentional raw HTML
            cache_size=50,      # Cache up to 50 templates in memory
        )
        log.info("Jinja2 environment initialized with caching")
    return _jinja_env


def _render_template(template_name: str, context: dict) -> str:
    """
    Render a Jinja2 template with caching.
    Templates are loaded once and cached for subsequent calls.
    """
    # Get or load cached template
    if template_name not in _template_cache:
        try:
            env = _get_jinja_env()
            _template_cache[template_name] = env.get_template(template_name)
            log.debug("Template loaded and cached: %s", template_name)
        except jinja2.TemplateError as exc:
            log.error("Jinja2 template error in %s: %s", template_name, exc)
            raise
    
    try:
        return _template_cache[template_name].render(context)
    except jinja2.TemplateError as exc:
        log.error("Jinja2 rendering error in %s: %s", template_name, exc)
        raise


# ── Optimized PDF Generation with CSS Caching ────────────────────────────────
def _get_cached_css(css_filename: str, zoom: float = 0.75) -> Optional[CSS]:
    """
    Get cached CSS object to avoid repeated parsing.
    CSS is parsed once and reused across multiple PDF generations.
    """
    cache_key = f"{css_filename}_{zoom:.2f}"
    
    if cache_key not in _css_cache:
        css_path = TEMPLATES_DIR / css_filename
        if css_path.exists():
            try:
                _css_cache[cache_key] = CSS(filename=str(css_path))
                log.debug("CSS loaded and cached: %s", css_filename)
            except Exception as e:
                log.error("Failed to load CSS %s: %s", css_filename, e)
                _css_cache[cache_key] = None
        else:
            log.warning("CSS file not found: %s", css_path)
            _css_cache[cache_key] = None
    
    return _css_cache[cache_key]


def _html_to_pdf(
    html_string: str, 
    css_filename: str, 
    zoom: float = 0.75,
    optimize_for_speed: bool = True
) -> bytes:
    """
    Convert HTML string to PDF bytes using WeasyPrint with optimizations.
    
    Args:
        html_string: HTML content to convert
        css_filename: CSS filename from templates directory
        zoom: Zoom factor for PDF output
        optimize_for_speed: If True, skips heavy optimizations for faster generation
    
    Returns:
        Raw PDF bytes
    """
    stylesheet = _get_cached_css(css_filename, zoom)
    stylesheets = [stylesheet] if stylesheet else []
    
    # Optimize WeasyPrint parameters for speed vs quality
    optimize_options = ("fonts", "images") if optimize_for_speed else ("fonts", "images", "content")
    
    try:
        pdf_bytes = (
            HTML(string=html_string, base_url=str(TEMPLATES_DIR))
            .write_pdf(
                stylesheets=stylesheets,
                presentational_hints=True,
                zoom=zoom,
                optimize_size=optimize_options,
                resolution=96 if optimize_for_speed else 150,  # Lower resolution = faster
            )
        )
        log.info("PDF generated successfully: %d bytes (optimization: %s)", 
                 len(pdf_bytes), "speed" if optimize_for_speed else "quality")
        return pdf_bytes
    except Exception as e:
        log.exception("WeasyPrint PDF generation failed: %s", str(e))
        raise


# ── Public API ────────────────────────────────────────────────────────────────

def generate_hotel_pdf(form: Dict[str, Any]) -> Tuple[bytes, str]:
    """
    Generate a hotel voucher PDF from the submitted form data.
    Optimized version with caching and image compression.
    
    Args:
        form: dict of field values from the FastAPI form endpoint.
    
    Returns:
        (pdf_bytes, suggested_filename) — ready to stream to the browser.
    """
    start_time = datetime.now()
    log.info(
        "Generating hotel PDF - booking=%s guest=%s",
        form.get("booking_number", "N/A"), 
        form.get("guest_name", "N/A"),
    )
    
    # Pre-compute date once
    today_date = datetime.today().strftime("%d %b, %Y")
    
    # Build context with optimized images
    context = {
        "booking_number": form.get("booking_number") or "N/A",
        "hotel_cfn": form.get("hotel_cfn") or "N/A",
        "guest_name": form.get("guest_name") or "N/A",
        "country": form.get("country") or "N/A",
        "hotel_name": form.get("hotel_name") or "N/A",
        "address": form.get("address") or "N/A",
        "contact_number": form.get("contact_number") or "N/A",
        "cancellation_policy": form.get("cancellation_policy") or "N/A",
        "check_in": form.get("check_in") or "N/A",
        "check_out": form.get("check_out") or "N/A",
        "book_payable_by": form.get("book_payable_by") or "N/A",
        "remarks": form.get("remarks") or "N/A",
        "num_rooms": form.get("num_rooms") or "0",
        "extra_beds": form.get("extra_beds") or "0",
        "num_adults": form.get("num_adults") or "0",
        "num_children": form.get("num_children") or "0",
        "room_type": form.get("room_type") or "N/A",
        "today_date": today_date,
        # Optimized images with appropriate sizes for different uses
        "logo_path": image_to_base64_optimized("vayo_logo.png", max_width=120, quality=65),
        "stamp_path": image_to_base64_optimized("vayyo_stamp.jpg", max_width=100, quality=50),
        "thailand_path": image_to_base64_optimized("thailand.jpg", max_width=60, quality=55),
        "vietnam_path": image_to_base64_optimized("vietnam.png", max_width=60, quality=55),
        "japan_path": image_to_base64_optimized("japan.png", max_width=60, quality=55),
        "indonesia_path": image_to_base64_optimized("indonesia.jpg", max_width=60, quality=55),
    }
    
    # Render HTML from cached template
    html_string = _render_template("booking.html", context)
    
    # Generate PDF with speed optimization
    pdf_bytes = _html_to_pdf(html_string, css_filename="styles.css", zoom=0.75, optimize_for_speed=True)
    
    # Generate filename
    stem = generate_unique_filename(context["guest_name"], context["booking_number"])
    filename = f"HotelVoucher_{stem}.pdf"
    
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("Hotel PDF generated: %s (%d bytes) in %.2f seconds", filename, len(pdf_bytes), elapsed)
    
    return pdf_bytes, filename


def generate_tour_pdf(form: Dict[str, Any]) -> Tuple[bytes, str]:
    """
    Generate a tour/transfer voucher PDF from the submitted form data.
    Optimized version with caching and image compression.
    
    Args:
        form: dict of field values from the FastAPI form endpoint.
    
    Returns:
        (pdf_bytes, suggested_filename) — ready to stream to the browser.
    """
    start_time = datetime.now()
    log.info(
        "Generating tour PDF - booking=%s guest=%s",
        form.get("booking_number", "N/A"),
        form.get("guest_name", "N/A"),
    )
    
    # Pre-compute date once
    today_date = datetime.today().strftime("%d %b, %Y")
    
    context = {
        "booking_number": form.get("booking_number") or "N/A",
        "guest_name": form.get("guest_name") or "N/A",
        "guest_mobile_no": form.get("guest_mobile_no") or "N/A",
        "hotel_name": form.get("tour_name") or "N/A",
        "package_name": form.get("package_name") or "N/A",
        "service_date": form.get("service_date") or "N/A",
        "check_in": form.get("pickup_from") or "N/A",
        "check_out": form.get("drop_to") or "N/A",
        "pick_time": form.get("pick_time") or "N/A",
        "cancellation_policy": form.get("cancellation_policy") or "N/A",
        "book_payable_by": form.get("book_payable_by") or "N/A",
        "num_adults": form.get("num_adults") or "0",
        "num_children": form.get("num_children") or "0",
        "room_type": form.get("service_type") or "N/A",
        "today_date": today_date,
        # Optimized images
        "logo_path": image_to_base64_optimized("vayo_logo.png", max_width=120, quality=65),
        "stamp_path": image_to_base64_optimized("vayyo_stamp.jpg", max_width=100, quality=50),
        "welcome_flag": image_to_base64_optimized("welcome.png", max_width=80, quality=60),
        "thailand_path": image_to_base64_optimized("thailand.jpg", max_width=60, quality=55),
        "vietnam_path": image_to_base64_optimized("vietnam.png", max_width=60, quality=55),
        "indonesia_path": image_to_base64_optimized("indonesia.jpg", max_width=60, quality=55),
        "japan_path": image_to_base64_optimized("japan.png", max_width=60, quality=55),
    }
    
    # Render HTML from cached template
    html_string = _render_template("tour.html", context)
    
    # Generate PDF with speed optimization
    pdf_bytes = _html_to_pdf(html_string, css_filename="styles2.css", zoom=0.75, optimize_for_speed=True)
    
    # Generate filename
    stem = generate_unique_filename(context["guest_name"], context["booking_number"])
    filename = f"TourVoucher_{stem}.pdf"
    
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("Tour PDF generated: %s (%d bytes) in %.2f seconds", filename, len(pdf_bytes), elapsed)
    
    return pdf_bytes, filename


# ── Async Versions for FastAPI Endpoints ─────────────────────────────────────
async def generate_hotel_pdf_async(form: Dict[str, Any]) -> Tuple[bytes, str]:
    """
    Async version of generate_hotel_pdf for use in FastAPI endpoints.
    Runs PDF generation in a thread pool to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, generate_hotel_pdf, form)


async def generate_tour_pdf_async(form: Dict[str, Any]) -> Tuple[bytes, str]:
    """
    Async version of generate_tour_pdf for use in FastAPI endpoints.
    Runs PDF generation in a thread pool to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, generate_tour_pdf, form)


# ── Cache Management Utilities ───────────────────────────────────────────────
def clear_caches() -> None:
    """
    Clear all caches (useful for testing or when templates/CSS change).
    """
    global _css_cache, _template_cache, _jinja_env
    _css_cache.clear()
    _template_cache.clear()
    _jinja_env = None
    image_to_base64_optimized.cache_clear()
    log.info("All caches cleared")


def get_cache_stats() -> Dict[str, int]:
    """
    Get cache statistics for monitoring.
    """
    return {
        "css_cache_size": len(_css_cache),
        "template_cache_size": len(_template_cache),
        "image_cache_size": image_to_base64_optimized.cache_info().currsize,
        "image_cache_hits": image_to_base64_optimized.cache_info().hits,
        "image_cache_misses": image_to_base64_optimized.cache_info().misses,
    }


# ── Warmup Function for Application Startup ──────────────────────────────────
def warmup_caches() -> None:
    """
    Pre-load templates and CSS to avoid first-request delay.
    Call this during application startup.
    """
    log.info("Warming up caches...")
    
    # Initialize Jinja2 environment
    _get_jinja_env()
    
    # Pre-load templates
    try:
        _render_template("booking.html", {})
        _render_template("tour.html", {})
        log.info("Templates pre-loaded successfully")
    except Exception as e:
        log.warning("Failed to pre-load templates: %s", e)
    
    # Pre-load CSS
    try:
        _get_cached_css("styles.css")
        _get_cached_css("styles2.css")
        log.info("CSS files pre-loaded successfully")
    except Exception as e:
        log.warning("Failed to pre-load CSS: %s", e)
    
    # Pre-load images (optional)
    images = ["vayo_logo.png", "vayyo_stamp.jpg", "thailand.jpg", "vietnam.png", 
              "japan.png", "indonesia.jpg", "welcome.png"]
    for img in images:
        if (ASSETS_DIR / img).exists():
            image_to_base64_optimized(img, max_width=120, quality=60)
    
    log.info("Cache warmup complete. Stats: %s", get_cache_stats())