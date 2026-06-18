"""
features/vstudio/router.py
───────────────────────────
VStudio — Social Media Content Studio for VIKRAM.

This feature helps clients create social media content for their brand.
Currently in beta — more features coming soon.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Import auth helpers from the shared auth module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from features.auth import require_auth

log = logging.getLogger("vikram.vstudio")
router = APIRouter()

# ── Templates ──────────────────────────────────────────────────────────────────
# Templates are in the root templates folder (same as main.py)
BASE = Path(__file__).parent.parent.parent  # Go up to project root
TEMPLATES_DIR = BASE / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@require_auth
async def vstudio_home(request: Request):
    """
    VStudio main page — Social media content creation studio.
    """
    return templates.TemplateResponse(
        "vstudio.html",
        {
            "request": request,
            "title": "VStudio — Social Media Studio",
        }
    )


@router.get("/health")
async def vstudio_health():
    """
    Health check endpoint for VStudio.
    """
    return {
        "feature": "vstudio",
        "status": "healthy",
        "version": "1.0.0",
        "description": "Social Media Content Studio",
    }