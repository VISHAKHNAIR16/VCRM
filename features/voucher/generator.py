"""
features/voucher/generator.py
──────────────────────────────
PDF generation logic for the VIKRAM Voucher Generator feature.
Optimized for performance with caching, image compression, and async support.
Enhanced with better image quality and dynamic header support.
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
from typing import Optional, Tuple, Dict, Any, List

import jinja2
from weasyprint import HTML, CSS
from PIL import Image, ImageEnhance, ImageFilter

log = logging.getLogger("vikram.voucher.generator")

# ── Paths ────────────────────────────────────────────────────────────────────
FEATURE_DIR = Path(__file__).parent
TEMPLATES_DIR = FEATURE_DIR / "templates"
ASSETS_DIR = FEATURE_DIR / "assets"

# ── Caches for performance ───────────────────────────────────────────────────
_css_cache: Dict[str, Optional[CSS]] = {}
_template_cache: Dict[str, jinja2.Template] = {}
_jinja_env: Optional[jinja2.Environment] = None

# Thread pool for async PDF generation (6 concurrent workers for better throughput)
_executor = ThreadPoolExecutor(max_workers=6)


# FilenameCounter removed — was a global dict that leaked memory across jobs
# and produced wrong counters after server restarts.
# Replaced with timestamp-based uniqueness (see generate_unique_filename below).


# ── Enhanced Image Helper with Quality Optimization ─────────────────────────
from datetime import datetime
import re

def format_date(value):
    """
    Convert any date string to dd/mm/yyyy format.
    Handles various input formats: YYYY-MM-DD, DD/MM/YYYY, etc.
    """
    if not value or value == "N/A" or value.strip() == "":
        return "N/A"
    
    value = str(value).strip()
    
    # Try to parse various date formats
    date_formats = [
        "%Y-%m-%d",           # 2024-01-15
        "%Y/%m/%d",           # 2024/01/15
        "%d-%m-%Y",           # 15-01-2024
        "%d/%m/%Y",           # 15/01/2024
        "%d-%b-%Y",           # 15-Jan-2024
        "%d %b %Y",           # 15 Jan 2024
        "%B %d, %Y",          # January 15, 2024
        "%d %B %Y",           # 15 January 2024
        "%m/%d/%Y",           # 01/15/2024 (US format)
        "%Y%m%d",             # 20240115
        "%d.%m.%Y",           # 15.01.2024
    ]
    
    # Also try to extract date from strings like "2024-01-15 14:30:00"
    # First, try to split on space and take the first part
    if " " in value:
        date_part = value.split(" ")[0]
        # Try parsing just the date part
        for fmt in date_formats:
            try:
                dt = datetime.strptime(date_part, fmt)
                return dt.strftime("%d/%m/%Y")
            except ValueError:
                continue
    
    # Try parsing the full string with all formats
    for fmt in date_formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    
    # If we can't parse it, return the original
    return value


@lru_cache(maxsize=64)
def image_to_base64_enhanced(
    filename: str, 
    max_width: int = 200, 
    quality: int = 85,
    enhance_contrast: bool = True,
    apply_sharpen: bool = True,
    force_reload: bool = False
) -> str:
    """
    Load, optimize, enhance and cache images as base64 data URIs.
    Enhanced version with better quality and sharpness for stamps/logos.
    
    Args:
        filename: Image filename in assets directory
        max_width: Maximum width in pixels (maintains aspect ratio)
        quality: JPEG quality (1-100, higher = better quality, larger file)
        enhance_contrast: Apply contrast enhancement for better clarity
        apply_sharpen: Apply sharpening filter for better definition
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
            # Convert to RGB if necessary
            original_mode = img.mode
            if img.mode not in ('RGB', 'L'):
                if img.mode == 'RGBA':
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3])
                    img = background
                else:
                    img = img.convert('RGB')
            
            # Apply enhancements for better clarity (especially for stamps)
            if enhance_contrast:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(1.2)  # 20% contrast boost
            
            if apply_sharpen and filename.lower().find('stamp') >= 0:
                # Stronger sharpening for stamps
                img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=150, threshold=3))
            elif apply_sharpen:
                # Gentle sharpening for logos
                img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=100, threshold=2))
            
            # Resize if image is larger than max_width
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                # Use LANCZOS for high-quality downsampling
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Save with appropriate compression
            buffer = io.BytesIO()
            
            # Use JPEG for photos, PNG for graphics with transparency
            use_jpeg = filename.lower().endswith(('.jpg', '.jpeg')) or original_mode != 'RGBA'
            
            if use_jpeg:
                # Higher quality for stamps and logos
                img.save(buffer, format='JPEG', quality=quality, optimize=True, subsampling=0)
                mime_type = 'image/jpeg'
            else:
                img.save(buffer, format='PNG', optimize=True)
                mime_type = 'image/png'
            
            data = base64.b64encode(buffer.getvalue()).decode('utf-8')
            return f"data:{mime_type};base64,{data}"
            
    except Exception as e:
        log.exception("Failed to encode asset: %s - %s", filename, str(e))
        return ""


# Legacy wrapper for backward compatibility
def image_to_base64(filename: str) -> str:
    """Legacy wrapper for image_to_base64_enhanced with balanced settings."""
    return image_to_base64_enhanced(filename, max_width=180, quality=80, enhance_contrast=True, apply_sharpen=True)


# ── Enhanced Unique Filename Generator with Counter Support ─────────────────
def generate_unique_filename(guest_name: str, booking_id: str, voucher_type: str = "voucher") -> str:
    """
    Produce a collision-resistant unique filename using a microsecond timestamp.
    Format: {type}_{booking_id}_{guest_initials}_{HHMMSSffffff}

    Using timestamps instead of the old class-level counter because the counter:
      - leaked memory indefinitely across bulk jobs
      - reset incorrectly on server restart mid-job
      - produced wrong suffixes if the same booking appeared in multiple files
    Microsecond precision makes accidental collisions effectively impossible.
    """
    guest_initials = re.sub(r"[^a-zA-Z]", "", (guest_name or "").strip())[:4].upper() or "GUEST"
    booking_id_clean = re.sub(r"[^a-zA-Z0-9]", "", booking_id or "")[:12] or "UNKNOWN"
    ts = datetime.now().strftime("%H%M%S%f")  # e.g. 142359123456
    return f"{voucher_type}_{booking_id_clean}_{guest_initials}_{ts}"


# ── Dynamic Header Parser for Excel Data ─────────────────────────────────────
def parse_service_description(service_text: str) -> Dict[str, str]:
    """
    Parse service description from Excel to extract meaningful header info.
    Returns dict with keys: main_title, subtitle, service_details
    """
    if not service_text:
        return {"main_title": "", "subtitle": "", "service_details": ""}
    
    result = {
        "main_title": "",
        "subtitle": "",
        "service_details": service_text[:200]  # Default truncation
    }
    
    # Extract main service type
    if "Airport" in service_text and "Hotel" in service_text:
        if "Pattaya" in service_text:
            result["main_title"] = "Airport to Pattaya Transfer"
        elif "Bangkok" in service_text:
            result["main_title"] = "Airport to Bangkok Transfer"
        else:
            result["main_title"] = "Airport Transfer Service"
    
    elif "Hotel" in service_text and "Airport" in service_text:
        result["main_title"] = "Hotel to Airport Transfer"
    
    elif "Coral Island" in service_text:
        result["main_title"] = "Coral Island Tour Package"
        result["subtitle"] = "Speed Boat & Indian Lunch Included"
    
    elif "Sanctuary of Truth" in service_text:
        result["main_title"] = "Sanctuary of Truth Tour"
        result["subtitle"] = "Cultural Heritage Experience"
    
    elif "Jurassic World" in service_text:
        result["main_title"] = "Jurassic World: The Experience"
        result["subtitle"] = "Adventure Tour"
    
    elif "Golden Buddha" in service_text or "Marble Buddha" in service_text:
        result["main_title"] = "Temple & Cultural Tour"
        result["subtitle"] = "Golden Buddha & Marble Buddha Visit"
    
    elif "Gems Gallery" in service_text:
        result["main_title"] = "Gems Gallery Visit"
    
    else:
        # Try to extract meaningful title from the description
        words = service_text.split()
        if len(words) > 5:
            result["main_title"] = " ".join(words[:4]) + "..."
        else:
            result["main_title"] = service_text[:100]
    
    return result


def extract_service_info_from_excel(row_data: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract service information from Excel row data for dynamic headers.
    """
    service_info = {
        "service_name": row_data.get("service", "") or row_data.get("tour_name", "") or row_data.get("Service", ""),
        "transfer_type": row_data.get("transfer_type", "") or row_data.get("Transfer Type", "") or row_data.get("Vehicle Type", ""),
        "from_location": row_data.get("from", "") or row_data.get("pickup_from", "") or row_data.get("From", ""),
        "to_location": row_data.get("to", "") or row_data.get("drop_to", "") or row_data.get("To", ""),
        "pickup_time": row_data.get("pick_up_time", "") or row_data.get("pick_time", "") or row_data.get("Pick Up Time", ""),
        "flight_number": row_data.get("flight_number", "") or row_data.get("Flight Number", ""),
    }
    
    # Parse service description for dynamic headers
    parsed = parse_service_description(service_info["service_name"])
    
    return {
        "main_title": parsed["main_title"] or service_info["service_name"][:50],
        "subtitle": parsed["subtitle"],
        "service_details": parsed["service_details"],
        "from_location": service_info["from_location"],
        "to_location": service_info["to_location"],
        "pickup_time": service_info["pickup_time"],
        "flight_number": service_info["flight_number"],
        "transfer_type": service_info["transfer_type"],
    }


# ── Jinja2 environment with caching ─────────────────────────────────────────
def _get_jinja_env() -> jinja2.Environment:
    """Lazy-load and cache Jinja2 environment."""
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=False,
            cache_size=50,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Register custom filters
        _jinja_env.filters['format_date'] = format_date
        log.info("Jinja2 environment initialized with caching")
    return _jinja_env


def _render_template(template_name: str, context: dict) -> str:
    """Render a Jinja2 template with caching."""
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
    """Get cached CSS object to avoid repeated parsing."""
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
    optimize_for_speed: bool = False  # Changed default to False for better quality
) -> bytes:
    """
    Convert HTML string to PDF bytes using WeasyPrint with optimizations.
    """
    stylesheet = _get_cached_css(css_filename, zoom)
    stylesheets = [stylesheet] if stylesheet else []
    
    # For better quality, don't skip optimizations
    optimize_options = ("fonts", "images") if optimize_for_speed else ("fonts",)
    
    try:
        pdf_bytes = (
            HTML(string=html_string, base_url=str(TEMPLATES_DIR))
            .write_pdf(
                stylesheets=stylesheets,
                presentational_hints=True,
                zoom=zoom,
                optimize_size=optimize_options,
                resolution=150 if not optimize_for_speed else 96,  # Higher resolution for better quality
            )
        )
        log.info("PDF generated successfully: %d bytes (optimization: %s)", 
                 len(pdf_bytes), "speed" if optimize_for_speed else "quality")
        return pdf_bytes
    except Exception as e:
        log.exception("WeasyPrint PDF generation failed: %s", str(e))
        raise


# ── Public API ────────────────────────────────────────────────────────────────

def generate_hotel_pdf(form: Dict[str, Any], excel_row_data: Optional[Dict[str, Any]] = None) -> Tuple[bytes, str]:
    """
    Generate a hotel voucher PDF from the submitted form data.
    Enhanced with dynamic headers from Excel data.
    
    Args:
        form: dict of field values from the FastAPI form endpoint.
        excel_row_data: Optional raw Excel row data for dynamic headers.
    
    Returns:
        (pdf_bytes, suggested_filename) — ready to stream to the browser.
    """
    start_time = datetime.now()
    
    # Extract dynamic service info if provided
    service_info = {}
    if excel_row_data:
        service_info = extract_service_info_from_excel(excel_row_data)
    
    log.info(
        "Generating hotel PDF - booking=%s guest=%s service=%s",
        form.get("booking_number", "N/A"), 
        form.get("guest_name", "N/A"),
        service_info.get("main_title", "N/A"),
    )
    
    today_date = datetime.today().strftime("%d %b, %Y")
    
    # Build context with enhanced images (better quality)
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
        # Dynamic service info from Excel
        "dynamic_title": service_info.get("main_title", ""),
        "dynamic_subtitle": service_info.get("subtitle", ""),
        "from_location": service_info.get("from_location", ""),
        "to_location": service_info.get("to_location", ""),
        "pickup_time": service_info.get("pickup_time", ""),
        "flight_number": service_info.get("flight_number", ""),
        # Enhanced images with better quality settings
        "logo_path": image_to_base64_enhanced("vayo_logo.png", max_width=150, quality=85, enhance_contrast=True, apply_sharpen=True),
        "stamp_path": image_to_base64_enhanced("vayyo_stamp.jpg", max_width=120, quality=90, enhance_contrast=True, apply_sharpen=True),
        "thailand_path": image_to_base64_enhanced("thailand.jpg", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
        "vietnam_path": image_to_base64_enhanced("vietnam.png", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
        "japan_path": image_to_base64_enhanced("japan.png", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
        "indonesia_path": image_to_base64_enhanced("indonesia.jpg", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
    }
    
    # Render HTML from cached template
    html_string = _render_template("booking.html", context)
    
    # Generate PDF with quality optimization (not speed)
    pdf_bytes = _html_to_pdf(html_string, css_filename="styles.css", zoom=0.8, optimize_for_speed=False)
    
    # Generate unique filename with counter
    stem = generate_unique_filename(
        context["guest_name"], 
        context["booking_number"], 
        "HOTEL"
    )
    filename = f"HotelVoucher_{stem}.pdf"
    
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("Hotel PDF generated: %s (%d bytes) in %.2f seconds", filename, len(pdf_bytes), elapsed)
    
    return pdf_bytes, filename


def generate_tour_pdf(form: Dict[str, Any], excel_row_data: Optional[Dict[str, Any]] = None) -> Tuple[bytes, str]:
    """
    Generate a tour/transfer voucher PDF from the submitted form data.
    Enhanced with dynamic headers from Excel data.
    
    Args:
        form: dict of field values from the FastAPI form endpoint.
        excel_row_data: Optional raw Excel row data for dynamic headers.
    
    Returns:
        (pdf_bytes, suggested_filename) — ready to stream to the browser.
    """
    start_time = datetime.now()
    
    # Extract dynamic service info if provided
    service_info = {}
    if excel_row_data:
        service_info = extract_service_info_from_excel(excel_row_data)
    
    log.info(
        "Generating tour PDF - booking=%s guest=%s service=%s",
        form.get("booking_number", "N/A"),
        form.get("guest_name", "N/A"),
        service_info.get("main_title", form.get("tour_name", "N/A")),
    )
    
    today_date = datetime.today().strftime("%d %b, %Y")
    
    # Use dynamic title if available, otherwise fall back to form data
    main_title = service_info.get("main_title") or form.get("tour_name") or "Tour Service"
    subtitle = service_info.get("subtitle") or ""
    
    context = {
        "booking_number": form.get("booking_number") or "N/A",
        "guest_name": form.get("guest_name") or "N/A",
        "guest_mobile_no": form.get("guest_mobile_no") or "N/A",
        "hotel_name": main_title,
        "package_name": form.get("package_name") or "N/A",
        "service_date": form.get("service_date") or service_info.get("service_date") or "N/A",
        "check_in": service_info.get("from_location") or form.get("pickup_from") or "N/A",
        "check_out": service_info.get("to_location") or form.get("drop_to") or "N/A",
        "pick_time": service_info.get("pickup_time") or form.get("pick_time") or "N/A",
        "cancellation_policy": form.get("cancellation_policy") or "N/A",
        "book_payable_by": form.get("book_payable_by") or "N/A",
        "num_adults": form.get("num_adults") or "0",
        "num_children": form.get("num_children") or "0",
        "room_type": service_info.get("transfer_type") or form.get("service_type") or "N/A",
        "today_date": today_date,
        # Dynamic headers
        "dynamic_title": main_title,
        "dynamic_subtitle": subtitle,
        "flight_number": service_info.get("flight_number", ""),
        # Enhanced images with better quality
        "logo_path": image_to_base64_enhanced("vayo_logo.png", max_width=150, quality=85, enhance_contrast=True, apply_sharpen=True),
        "stamp_path": image_to_base64_enhanced("vayyo_stamp.jpg", max_width=120, quality=90, enhance_contrast=True, apply_sharpen=True),
        "welcome_flag": image_to_base64_enhanced("welcome.png", max_width=100, quality=85, enhance_contrast=True, apply_sharpen=True),
        "thailand_path": image_to_base64_enhanced("thailand.jpg", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
        "vietnam_path": image_to_base64_enhanced("vietnam.png", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
        "indonesia_path": image_to_base64_enhanced("indonesia.jpg", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
        "japan_path": image_to_base64_enhanced("japan.png", max_width=80, quality=80, enhance_contrast=False, apply_sharpen=False),
    }
    
    # Render HTML from cached template
    html_string = _render_template("tour.html", context)
    
    # Generate PDF with quality optimization
    pdf_bytes = _html_to_pdf(html_string, css_filename="styles2.css", zoom=0.8, optimize_for_speed=False)
    
    # Generate unique filename with counter
    stem = generate_unique_filename(
        context["guest_name"], 
        context["booking_number"], 
        "TOUR"
    )
    filename = f"TourVoucher_{stem}.pdf"
    
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("Tour PDF generated: %s (%d bytes) in %.2f seconds", filename, len(pdf_bytes), elapsed)
    
    return pdf_bytes, filename


# ── Async Versions for FastAPI Endpoints ─────────────────────────────────────
async def generate_hotel_pdf_async(form: Dict[str, Any], excel_row_data: Optional[Dict[str, Any]] = None) -> Tuple[bytes, str]:
    """
    Async version of generate_hotel_pdf for use in FastAPI endpoints.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, generate_hotel_pdf, form, excel_row_data)


async def generate_tour_pdf_async(form: Dict[str, Any], excel_row_data: Optional[Dict[str, Any]] = None) -> Tuple[bytes, str]:
    """
    Async version of generate_tour_pdf for use in FastAPI endpoints.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, generate_tour_pdf, form, excel_row_data)


# ── Cache Management Utilities ───────────────────────────────────────────────
def clear_caches() -> None:
    """Clear all caches."""
    global _css_cache, _template_cache, _jinja_env
    _css_cache.clear()
    _template_cache.clear()
    _jinja_env = None
    image_to_base64_enhanced.cache_clear()
    log.info("All caches cleared")


def get_cache_stats() -> Dict[str, int]:
    """Get cache statistics for monitoring."""
    return {
        "css_cache_size": len(_css_cache),
        "template_cache_size": len(_template_cache),
        "image_cache_size": image_to_base64_enhanced.cache_info().currsize,
        "image_cache_hits": image_to_base64_enhanced.cache_info().hits,
        "image_cache_misses": image_to_base64_enhanced.cache_info().misses,
    }


# ── Warmup Function for Application Startup ──────────────────────────────────
def warmup_caches() -> None:
    """Pre-load templates, CSS, and images to avoid first-request delay."""
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
    
    # Pre-load images with enhanced settings
    images = [
        "vayo_logo.png", "vayyo_stamp.jpg", "thailand.jpg", 
        "vietnam.png", "japan.png", "indonesia.jpg", "welcome.png"
    ]
    for img in images:
        if (ASSETS_DIR / img).exists():
            image_to_base64_enhanced(img, max_width=150, quality=85, enhance_contrast=True, apply_sharpen=True)
    
    log.info("Cache warmup complete. Stats: %s", get_cache_stats())