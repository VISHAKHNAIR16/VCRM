"""
features/voucher/router.py
───────────────────────────
FastAPI router for the VIKRAM Voucher Generator feature.

Routes:
  GET  /voucher              → voucher landing page (select hotel or tour)
  GET  /voucher/hotel        → hotel voucher form
  POST /voucher/hotel/pdf    → generate + download hotel PDF
  GET  /voucher/tour         → tour voucher form
  POST /voucher/tour/pdf     → generate + download tour PDF

All routes are protected by the same require_auth decorator used in main.py.
The router is mounted at /voucher by main.py.
"""

import io
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from features.voucher.generator import generate_hotel_pdf, generate_tour_pdf

log      = logging.getLogger("vikram.voucher.router")
router   = APIRouter()

# Voucher web-form templates live in templates/voucher/
# We resolve relative to this file so the module is self-contained.
_TMPL_DIR = Path(__file__).parent.parent.parent / "templates" / "voucher"
templates = Jinja2Templates(directory=str(_TMPL_DIR))

# Re-use require_auth from main — imported at call time to avoid circular imports
def _get_require_auth():
    from main import require_auth
    return require_auth


# ── Voucher home ──────────────────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def voucher_home(request: Request):
    auth = _get_require_auth()

    @auth
    async def _inner(request: Request):
        return templates.TemplateResponse("voucher_home.html", {"request": request})

    return await _inner(request)


# ── Hotel voucher form ────────────────────────────────────────────────────────
@router.get("/hotel", response_class=HTMLResponse)
async def hotel_form(request: Request):
    auth = _get_require_auth()

    @auth
    async def _inner(request: Request):
        return templates.TemplateResponse("hotel_form.html", {"request": request})

    return await _inner(request)


@router.post("/hotel/pdf")
async def hotel_pdf(
    request:             Request,
    booking_number:      str = Form(""),
    hotel_cfn:           str = Form(""),
    guest_name:          str = Form(""),
    country:             str = Form(""),
    hotel_name:          str = Form(""),
    address:             str = Form(""),
    contact_number:      str = Form(""),
    cancellation_policy: str = Form(""),
    check_in:            str = Form(""),
    check_out:           str = Form(""),
    book_payable_by:     str = Form(""),
    remarks:             str = Form(""),
    num_rooms:           str = Form("0"),
    extra_beds:          str = Form("0"),
    num_adults:          str = Form("0"),
    num_children:        str = Form("0"),
    room_type:           str = Form(""),
):
    auth = _get_require_auth()

    @auth
    async def _inner(request: Request):
        form_data = {
            "booking_number":      booking_number,
            "hotel_cfn":           hotel_cfn,
            "guest_name":          guest_name,
            "country":             country,
            "hotel_name":          hotel_name,
            "address":             address,
            "contact_number":      contact_number,
            "cancellation_policy": cancellation_policy,
            "check_in":            check_in,
            "check_out":           check_out,
            "book_payable_by":     book_payable_by,
            "remarks":             remarks,
            "num_rooms":           num_rooms,
            "extra_beds":          extra_beds,
            "num_adults":          num_adults,
            "num_children":        num_children,
            "room_type":           room_type,
        }

        log.info(
            "Hotel PDF request  booking=%s  guest=%s",
            booking_number, guest_name,
        )

        try:
            pdf_bytes, filename = generate_hotel_pdf(form_data)
        except Exception as exc:
            log.exception("Hotel PDF generation failed")
            return HTMLResponse(
                content=f"<h2>PDF generation failed</h2><pre>{exc}</pre>",
                status_code=500,
            )

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                # 'inline' → opens in browser tab; change to 'attachment' to force download
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length":      str(len(pdf_bytes)),
            },
        )

    return await _inner(request)


# ── Tour voucher form ─────────────────────────────────────────────────────────
@router.get("/tour", response_class=HTMLResponse)
async def tour_form(request: Request):
    auth = _get_require_auth()

    @auth
    async def _inner(request: Request):
        return templates.TemplateResponse("tour_form.html", {"request": request})

    return await _inner(request)


@router.post("/tour/pdf")
async def tour_pdf(
    request:             Request,
    booking_number:      str = Form(""),
    guest_name:          str = Form(""),
    guest_mobile_no:     str = Form(""),
    tour_name:           str = Form(""),
    package_name:        str = Form(""),
    service_date:        str = Form(""),
    pickup_from:         str = Form(""),
    drop_to:             str = Form(""),
    pick_time:           str = Form(""),
    cancellation_policy: str = Form(""),
    book_payable_by:     str = Form(""),
    num_adults:          str = Form("0"),
    num_children:        str = Form("0"),
    service_type:        str = Form("Private Transfer"),
):
    auth = _get_require_auth()

    @auth
    async def _inner(request: Request):
        form_data = {
            "booking_number":      booking_number,
            "guest_name":          guest_name,
            "guest_mobile_no":     guest_mobile_no,
            "tour_name":           tour_name,
            "package_name":        package_name,
            "service_date":        service_date,
            "pickup_from":         pickup_from,
            "drop_to":             drop_to,
            "pick_time":           pick_time,
            "cancellation_policy": cancellation_policy,
            "book_payable_by":     book_payable_by,
            "num_adults":          num_adults,
            "num_children":        num_children,
            "service_type":        service_type,
        }

        log.info(
            "Tour PDF request  booking=%s  guest=%s",
            booking_number, guest_name,
        )

        try:
            pdf_bytes, filename = generate_tour_pdf(form_data)
        except Exception as exc:
            log.exception("Tour PDF generation failed")
            return HTMLResponse(
                content=f"<h2>PDF generation failed</h2><pre>{exc}</pre>",
                status_code=500,
            )

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length":      str(len(pdf_bytes)),
            },
        )

    return await _inner(request)
