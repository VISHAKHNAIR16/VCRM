"""
features/voucher/generator.py
──────────────────────────────
PDF generation logic for the VIKRAM Voucher Generator feature.

This module is the direct replacement for the WeasyPrint logic that
previously lived in the desktop app's printReceipt() and print_tour_receipt()
functions. All business logic (base64 encoding, Jinja2 rendering, filename
generation) is ported here unchanged so the PDF output is identical.
"""

import base64
import io
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import jinja2
from weasyprint import HTML, CSS

log = logging.getLogger("vikram.voucher.generator")

# ── Paths ────────────────────────────────────────────────────────────────────
FEATURE_DIR   = Path(__file__).parent
TEMPLATES_DIR = FEATURE_DIR / "templates"
ASSETS_DIR    = FEATURE_DIR / "assets"


# ── Image helper (identical to the desktop app's image_to_base64) ────────────
def image_to_base64(filename: str) -> str:
    """
    Load an image from the assets directory and return a data-URI string
    suitable for embedding directly in an HTML <img src="..."> attribute.

    Returns an empty string if the file doesn't exist or can't be read,
    so the template's {% if logo_path %} guards handle the fallback cleanly.
    """
    if not filename:
        return ""
    path = ASSETS_DIR / filename
    if not path.exists():
        log.warning("Asset not found: %s", path)
        return ""
    try:
        ext      = path.suffix.lower()
        mime_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif"}
        mime     = mime_map.get(ext, "jpeg")
        data     = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:image/{mime};base64,{data}"
    except Exception:
        log.exception("Failed to encode asset: %s", filename)
        return ""


# ── Filename generator (identical to desktop app) ────────────────────────────
def generate_unique_filename(guest_name: str, booking_id: str) -> str:
    """
    Produce a deterministic but collision-resistant filename stem.
    Uses characters 4-5 of the guest name + first 8 chars of the booking ID.
    """
    guest_name = (guest_name or "").strip()
    prefix     = guest_name[3:5].upper() if len(guest_name) >= 5 else "XX"
    suffix     = re.sub(r"[^a-zA-Z0-9]", "", booking_id or "")[:8] or "00000000"
    return f"{suffix}{prefix}"


# ── Jinja2 environment pointing at the feature's own templates dir ───────────
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,   # HTML templates contain intentional raw HTML
)


def _render_template(template_name: str, context: dict) -> str:
    """Render a Jinja2 template and return the HTML string."""
    try:
        tpl = _jinja_env.get_template(template_name)
        return tpl.render(context)
    except jinja2.TemplateError as exc:
        log.error("Jinja2 template error in %s: %s", template_name, exc)
        raise


def _html_to_pdf(html_string: str, css_filename: str, zoom: float = 0.75) -> bytes:
    """
    Convert an HTML string to PDF bytes using WeasyPrint.
    base_url is set to TEMPLATES_DIR so relative CSS @imports resolve correctly.
    Returns raw PDF bytes.
    """
    css_path = TEMPLATES_DIR / css_filename
    if not css_path.exists():
        log.warning("CSS file not found: %s — rendering without stylesheet", css_path)
        stylesheets = []
    else:
        stylesheets = [CSS(filename=str(css_path))]

    try:
        pdf_bytes = (
            HTML(string=html_string, base_url=str(TEMPLATES_DIR))
            .write_pdf(
                stylesheets=stylesheets,
                presentational_hints=True,
                zoom=zoom,
                optimize_size=("fonts", "images", "content"),
            )
        )
        log.info("WeasyPrint PDF generated: %d bytes", len(pdf_bytes))
        return pdf_bytes
    except Exception:
        log.exception("WeasyPrint PDF generation failed")
        raise


# ── Public API ────────────────────────────────────────────────────────────────

def generate_hotel_pdf(form: dict) -> tuple[bytes, str]:
    """
    Generate a hotel voucher PDF from the submitted form data.

    Args:
        form: dict of field values from the FastAPI form endpoint.

    Returns:
        (pdf_bytes, suggested_filename) — ready to stream to the browser.
    """
    log.info(
        "Generating hotel PDF  booking=%s  guest=%s",
        form.get("booking_number"), form.get("guest_name"),
    )

    context = {
        "booking_number":      form.get("booking_number")  or "N/A",
        "hotel_cfn":           form.get("hotel_cfn")       or "N/A",
        "guest_name":          form.get("guest_name")      or "N/A",
        "country":             form.get("country")         or "N/A",
        "hotel_name":          form.get("hotel_name")      or "N/A",
        "address":             form.get("address")         or "N/A",
        "contact_number":      form.get("contact_number")  or "N/A",
        "cancellation_policy": form.get("cancellation_policy") or "N/A",
        "check_in":            form.get("check_in")        or "N/A",
        "check_out":           form.get("check_out")       or "N/A",
        "book_payable_by":     form.get("book_payable_by") or "N/A",
        "remarks":             form.get("remarks")         or "N/A",
        "num_rooms":           form.get("num_rooms")       or "0",
        "extra_beds":          form.get("extra_beds")      or "0",
        "num_adults":          form.get("num_adults")      or "0",
        "num_children":        form.get("num_children")    or "0",
        "room_type":           form.get("room_type")       or "N/A",
        "today_date":          datetime.today().strftime("%d %b, %Y"),
        # Images embedded as base64 so WeasyPrint doesn't need filesystem access
        "logo_path":           image_to_base64("vayo_logo.png"),
        "stamp_path":          image_to_base64("vayyo_stamp.jpg"),
        "thailand_path":       image_to_base64("thailand.jpg"),
        "vietnam_path":        image_to_base64("vietnam.png"),
        "japan_path":          image_to_base64("japan.png"),
        "indonesia_path":      image_to_base64("indonesia.jpg"),
    }

    html_string = _render_template("booking.html", context)
    pdf_bytes   = _html_to_pdf(html_string, css_filename="styles.css", zoom=0.75)

    stem     = generate_unique_filename(context["guest_name"], context["booking_number"])
    filename = f"HotelVoucher_{stem}.pdf"

    log.info("Hotel PDF ready: %s (%d bytes)", filename, len(pdf_bytes))
    return pdf_bytes, filename


def generate_tour_pdf(form: dict) -> tuple[bytes, str]:
    """
    Generate a tour/transfer voucher PDF from the submitted form data.

    Args:
        form: dict of field values from the FastAPI form endpoint.

    Returns:
        (pdf_bytes, suggested_filename) — ready to stream to the browser.
    """
    log.info(
        "Generating tour PDF  booking=%s  guest=%s",
        form.get("booking_number"), form.get("guest_name"),
    )

    context = {
        "booking_number":      form.get("booking_number")      or "N/A",
        "guest_name":          form.get("guest_name")          or "N/A",
        "guest_mobile_no":     form.get("guest_mobile_no")     or "N/A",
        "hotel_name":          form.get("tour_name")           or "N/A",   # tour_name maps to hotel_name in template
        "package_name":        form.get("package_name")        or "N/A",
        "service_date":        form.get("service_date")        or "N/A",
        "check_in":            form.get("pickup_from")         or "N/A",   # pickup_from → check_in in template
        "check_out":           form.get("drop_to")             or "N/A",   # drop_to → check_out in template
        "pick_time":           form.get("pick_time")           or "N/A",
        "cancellation_policy": form.get("cancellation_policy") or "N/A",
        "book_payable_by":     form.get("book_payable_by")     or "N/A",
        "num_adults":          form.get("num_adults")          or "0",
        "num_children":        form.get("num_children")        or "0",
        "room_type":           form.get("service_type")        or "N/A",   # service_type → room_type in template
        "today_date":          datetime.today().strftime("%d %b, %Y"),
        # Images
        "logo_path":           image_to_base64("vayo_logo.png"),
        "stamp_path":          image_to_base64("vayyo_stamp.jpg"),
        "welcome_flag":        image_to_base64("welcome.png"),
        "thailand_path":       image_to_base64("thailand.jpg"),
        "vietnam_path":        image_to_base64("vietnam.png"),
        "indonesia_path":      image_to_base64("indonesia.jpg"),
        "japan_path":          image_to_base64("japan.png"),
    }

    html_string = _render_template("tour.html", context)
    pdf_bytes   = _html_to_pdf(html_string, css_filename="styles2.css", zoom=1.0)

    stem     = generate_unique_filename(context["guest_name"], context["booking_number"])
    filename = f"TourVoucher_{stem}.pdf"

    log.info("Tour PDF ready: %s (%d bytes)", filename, len(pdf_bytes))
    return pdf_bytes, filename
