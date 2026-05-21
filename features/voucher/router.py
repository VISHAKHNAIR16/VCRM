"""
features/voucher/router.py
───────────────────────────
FastAPI router for the VIKRAM Voucher Generator feature.
"""

import io
import logging
import os
import uuid
import asyncio
import json
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import UploadFile, File, Form as FastAPIForm

# Import optimized generator functions
from features.voucher.generator import (
    generate_hotel_pdf,
    generate_tour_pdf,
    generate_hotel_pdf_async,
    generate_tour_pdf_async,
    get_cache_stats,
    clear_caches,
    warmup_caches,
)

# Import bulk processing functions
from .bulk_processor import (
    process_bulk_upload,
    get_job_status,
    _job_store,
    save_zip_to_temp,
)

log = logging.getLogger("vikram.voucher.router")
router = APIRouter()

# Voucher web-form templates
_TMPL_DIR = Path(__file__).parent.parent.parent / "templates" / "voucher"
templates = Jinja2Templates(directory=str(_TMPL_DIR))


# ── Authentication ─────────────────────────────────────────────────────────────
async def verify_auth(request: Request) -> bool:
    """Dependency to verify authentication."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        log.warning("Unauthenticated access attempt to %s", request.url.path)
        return False
    return True


async def require_auth_dependency(request: Request):
    """FastAPI dependency for authentication."""
    is_authenticated = await verify_auth(request)
    if not is_authenticated:
        return RedirectResponse("/login", status_code=302)
    return True


async def render_with_auth(request: Request, template_name: str, context: dict = None):
    """Render template with authentication check."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    if context is None:
        context = {}
    context["request"] = request
    return templates.TemplateResponse(template_name, context)


# ── Voucher home ──────────────────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def voucher_home(request: Request):
    """Voucher generator landing page."""
    return await render_with_auth(request, "voucher_home.html")


# ── Hotel voucher form ────────────────────────────────────────────────────────
@router.get("/hotel", response_class=HTMLResponse)
async def hotel_form(request: Request):
    """Display hotel voucher form."""
    return await render_with_auth(request, "hotel_form.html")


@router.post("/hotel/pdf")
async def hotel_pdf(
    request: Request,
    booking_number: str = Form(""),
    hotel_cfn: str = Form(""),
    guest_name: str = Form(""),
    country: str = Form(""),
    hotel_name: str = Form(""),
    address: str = Form(""),
    contact_number: str = Form(""),
    cancellation_policy: str = Form(""),
    check_in: str = Form(""),
    check_out: str = Form(""),
    book_payable_by: str = Form(""),
    remarks: str = Form(""),
    num_rooms: str = Form("0"),
    extra_beds: str = Form("0"),
    num_adults: str = Form("0"),
    num_children: str = Form("0"),
    room_type: str = Form(""),
    async_mode: Optional[str] = Form("true"),
):
    """Generate hotel voucher PDF."""
    from main import get_token_from_request, verify_session_token
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    start_time = datetime.now()
    
    if not booking_number or not guest_name or not hotel_name:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: booking_number, guest_name, and hotel_name are required."
        )
    
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
        "num_rooms": num_rooms or "0",
        "extra_beds": extra_beds or "0",
        "num_adults": num_adults or "0",
        "num_children": num_children or "0",
        "room_type": room_type,
    }
    
    try:
        if async_mode.lower() == "true":
            pdf_bytes, filename = await generate_hotel_pdf_async(form_data)
        else:
            pdf_bytes, filename = generate_hotel_pdf(form_data)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        log.info("Hotel PDF generated in %.2f seconds: %s", elapsed, filename)
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                'Content-Disposition': f'inline; filename="{filename}"',
                'Content-Length': str(len(pdf_bytes)),
                'X-Generation-Time': str(elapsed),
            },
        )
    except Exception as exc:
        log.exception("Hotel PDF generation failed for booking %s", booking_number)
        return HTMLResponse(
            content=f"<h2>PDF generation failed</h2><pre>{str(exc)}</pre>",
            status_code=500,
        )


# ── Tour voucher form ─────────────────────────────────────────────────────────
@router.get("/tour", response_class=HTMLResponse)
async def tour_form(request: Request):
    """Display tour voucher form."""
    return await render_with_auth(request, "tour_form.html")


@router.post("/tour/pdf")
async def tour_pdf(
    request: Request,
    booking_number: str = Form(""),
    guest_name: str = Form(""),
    guest_mobile_no: str = Form(""),
    tour_name: str = Form(""),
    package_name: str = Form(""),
    service_date: str = Form(""),
    pickup_from: str = Form(""),
    drop_to: str = Form(""),
    pick_time: str = Form(""),
    cancellation_policy: str = Form(""),
    book_payable_by: str = Form(""),
    num_adults: str = Form("0"),
    num_children: str = Form("0"),
    service_type: str = Form("Private Transfer"),
    async_mode: Optional[str] = Form("true"),
):
    """Generate tour/transfer voucher PDF."""
    from main import get_token_from_request, verify_session_token
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    start_time = datetime.now()
    
    if not booking_number or not guest_name or not tour_name:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: booking_number, guest_name, and tour_name are required."
        )
    
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
        "num_adults": num_adults or "0",
        "num_children": num_children or "0",
        "service_type": service_type,
    }
    
    try:
        if async_mode.lower() == "true":
            pdf_bytes, filename = await generate_tour_pdf_async(form_data)
        else:
            pdf_bytes, filename = generate_tour_pdf(form_data)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        log.info("Tour PDF generated in %.2f seconds: %s", elapsed, filename)
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(len(pdf_bytes)),
                "X-Generation-Time": str(elapsed),
            },
        )
    except Exception as exc:
        log.exception("Tour PDF generation failed for booking %s", booking_number)
        return HTMLResponse(
            content=f"<h2>PDF generation failed</h2><pre>{str(exc)}</pre>",
            status_code=500,
        )


# ── Cache Management Endpoints ────────────────────────────────────────────────
@router.get("/cache-stats")
async def cache_stats(request: Request):
    """Get cache performance statistics."""
    from main import get_token_from_request, verify_session_token
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    try:
        stats = get_cache_stats()
        return JSONResponse({
            "ok": True,
            "stats": stats,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        log.error("Failed to get cache stats: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/clear-cache")
async def clear_cache(request: Request):
    """Clear all caches."""
    from main import get_token_from_request, verify_session_token
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    try:
        clear_caches()
        return JSONResponse({"ok": True, "message": "All caches cleared successfully"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/warmup")
async def warmup(request: Request):
    """Manually trigger cache warmup."""
    from main import get_token_from_request, verify_session_token
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    try:
        warmup_caches()
        stats = get_cache_stats()
        return JSONResponse({"ok": True, "message": "Caches warmed up", "stats": stats})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Bulk Voucher Generation Routes ────────────────────────────────────────────

@router.get("/bulk", response_class=HTMLResponse)
async def bulk_upload_page(request: Request):
    """Display bulk upload interface."""
    return await render_with_auth(request, "bulk_upload.html")


@router.post("/bulk/upload")
async def bulk_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    voucher_type: str = FastAPIForm(...),
):
    """Upload Excel/CSV file for bulk voucher generation."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(400, "Invalid file type. Please upload .xlsx, .xls, or .csv files only.")
    
    if voucher_type not in ['hotel', 'tour']:
        raise HTTPException(400, "voucher_type must be 'hotel' or 'tour'")
    
    # Read file content
    file_content = await file.read()
    
    # Create job ID
    job_id = str(uuid.uuid4())
    
    # Store initial job
    from .bulk_processor import BulkJob
    _job_store[job_id] = BulkJob(
        job_id=job_id,
        voucher_type=voucher_type,
        total_rows=0,
        status='pending',
        file_name=file.filename
    )
    
    log.info(f"Created bulk job {job_id} for file {file.filename}")
    
    # Process in background
    background_tasks.add_task(
        _process_bulk_background,
        file_content,
        file.filename,
        voucher_type,
        job_id
    )
    
    # Return immediately
    return JSONResponse({
        "ok": True,
        "job_id": job_id,
        "message": "Bulk generation started",
    })


async def _process_bulk_background(file_content: bytes, filename: str, voucher_type: str, job_id: str):
    """Background task to process bulk upload with immediate status updates."""
    try:
        # Update job to processing state immediately
        job = _job_store.get(job_id)
        if job:
            job.status = 'processing'
            job.update()
        
        log.info(f"Starting background processing for job {job_id}")
        
        # Process the bulk upload
        zip_bytes, zip_filename = await process_bulk_upload(
            file_content=file_content,
            filename=filename,
            voucher_type=voucher_type,
            job_id=job_id,
            on_progress=None
        )
        
        # Save to temp file
        temp_path = save_zip_to_temp(zip_bytes, zip_filename)
        
        # Update job with results
        job = _job_store.get(job_id)
        if job:
            job.output_zip_path = temp_path
            job.output_zip_filename = zip_filename
            job.status = 'completed'
            job.completed_at = datetime.now()
            job.update()
            
        log.info(f"Bulk job {job_id} completed successfully. ZIP: {zip_filename}")
            
    except Exception as e:
        log.exception("Background processing failed for job %s", job_id)
        job = _job_store.get(job_id)
        if job:
            job.status = 'failed'
            job.errors.append({'error': str(e), 'timestamp': datetime.now().isoformat()})
            job.update()


@router.get("/bulk/status/{job_id}")
async def bulk_status(request: Request, job_id: str):
    """Get status of a bulk generation job."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JSONResponse(status)


@router.get("/bulk/download/{job_id}")
async def bulk_download(request: Request, job_id: str):
    """Download the generated ZIP file."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    job = _job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != 'completed':
        raise HTTPException(status_code=400, detail=f"Job not ready. Status: {job.status}")
    
    if not job.output_zip_path or not os.path.exists(job.output_zip_path):
        raise HTTPException(status_code=404, detail="Output file not found")
    
    with open(job.output_zip_path, 'rb') as f:
        zip_bytes = f.read()
    
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job.output_zip_filename}"'},
    )


@router.get("/bulk/progress/{job_id}", response_class=HTMLResponse)
async def bulk_progress_page(request: Request, job_id: str):
    """Progress tracking page for bulk generation."""
    return await render_with_auth(request, "bulk_progress.html", {"job_id": job_id})


@router.get("/bulk/template")
async def download_template(request: Request, type: str = "hotel"):
    """Download Excel template for bulk upload."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    import pandas as pd
    
    if type == "hotel":
        columns = [
            'booking_number', 'hotel_cfn', 'guest_name', 'country', 'hotel_name',
            'address', 'contact_number', 'cancellation_policy', 'check_in', 'check_out',
            'book_payable_by', 'remarks', 'num_rooms', 'extra_beds', 'num_adults',
            'num_children', 'room_type'
        ]
        df = pd.DataFrame(columns=columns)
        sample_row = {
            'booking_number': 'HOTEL-001',
            'hotel_cfn': 'CFN-1001',
            'guest_name': 'John Doe',
            'country': 'USA',
            'hotel_name': 'Grand Plaza Hotel',
            'address': '123 Main Street',
            'contact_number': '+1 212-555-0100',
            'cancellation_policy': 'Free cancellation up to 48 hours',
            'check_in': '2024-01-15',
            'check_out': '2024-01-18',
            'book_payable_by': 'Company Account',
            'remarks': 'VIP guest - welcome drink',
            'num_rooms': '2',
            'extra_beds': '0',
            'num_adults': '2',
            'num_children': '0',
            'room_type': 'Deluxe King'
        }
        df = pd.concat([df, pd.DataFrame([sample_row])], ignore_index=True)
    else:
        columns = [
            'booking_number', 'guest_name', 'guest_mobile_no', 'tour_name', 'package_name',
            'service_date', 'pickup_from', 'drop_to', 'pick_time', 'cancellation_policy',
            'book_payable_by', 'num_adults', 'num_children', 'service_type'
        ]
        df = pd.DataFrame(columns=columns)
        sample_row = {
            'booking_number': 'TOUR-001',
            'guest_name': 'John Doe',
            'guest_mobile_no': '+1 212-555-0100',
            'tour_name': 'City Highlights Tour',
            'package_name': 'Premium Package',
            'service_date': '2024-01-20',
            'pickup_from': 'Hotel Lobby',
            'drop_to': 'Airport',
            'pick_time': '09:00 AM',
            'cancellation_policy': 'Free cancellation up to 24 hours',
            'book_payable_by': 'Guest',
            'num_adults': '2',
            'num_children': '0',
            'service_type': 'Private Transfer'
        }
        df = pd.concat([df, pd.DataFrame([sample_row])], ignore_index=True)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Vouchers', index=False)
    
    output.seek(0)
    filename = f"bulk_voucher_template_{type}.xlsx"
    
    return StreamingResponse(
        io.BytesIO(output.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/bulk/stream-status/{job_id}")
async def stream_bulk_status(request: Request, job_id: str):
    """Stream real-time status updates using Server-Sent Events (SSE)."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    async def event_generator():
        last_status_hash = None
        
        while True:
            try:
                status = get_job_status(job_id)
                
                if status:
                    status_hash = hash((
                        status.get('processed_rows', 0),
                        status.get('successful', 0),
                        status.get('failed', 0),
                        status.get('status', ''),
                        len(status.get('errors', []))
                    ))
                    
                    if status_hash != last_status_hash:
                        yield f"data: {json.dumps(status)}\n\n"
                        last_status_hash = status_hash
                        
                        if status['status'] in ['completed', 'failed']:
                            break
                
                await asyncio.sleep(1)
                
            except Exception as e:
                log.error(f"SSE stream error for job {job_id}: {e}")
                await asyncio.sleep(2)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/bulk/test-upload")
async def bulk_test_upload(request: Request):
    """Test endpoint to simulate bulk upload response."""
    from main import get_token_from_request, verify_session_token
    
    token = get_token_from_request(request)
    if not token or not verify_session_token(token):
        return RedirectResponse("/login", status_code=302)
    
    import uuid
    job_id = str(uuid.uuid4())
    
    return JSONResponse({
        "ok": True,
        "job_id": job_id,
        "message": "Test bulk generation started"
    })

# ── Health check ──────────────────────────────────────────────────────────────
@router.get("/health")
async def voucher_health():
    """Health check endpoint."""
    try:
        stats = get_cache_stats()
        return {
            "feature": "voucher-generator",
            "status": "healthy",
            "cache_stats": stats,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"feature": "voucher-generator", "status": "unhealthy", "error": str(e)}