"""
features/quotation_attractions
──────────────────────────────
Attraction Quotation Module

Provides:
  - Browse attractions with search/filters
  - View attraction details with pricing
  - Calculate ticket-only and ticket+transfer prices
  - Transfer options by city
"""

from .router import router
from . import db, pricing

__all__ = ["router", "db", "pricing"]