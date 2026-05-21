"""Pydantic models for voucher data validation."""
from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime


class HotelVoucherData(BaseModel):
    """Validation schema for hotel voucher data."""
    booking_number: str = Field(..., min_length=1, max_length=50)
    hotel_cfn: Optional[str] = ""
    guest_name: str = Field(..., min_length=1, max_length=100)
    country: Optional[str] = ""
    hotel_name: str = Field(..., min_length=1, max_length=200)
    address: Optional[str] = ""
    contact_number: Optional[str] = ""
    cancellation_policy: Optional[str] = ""
    check_in: Optional[str] = ""
    check_out: Optional[str] = ""
    book_payable_by: Optional[str] = ""
    remarks: Optional[str] = ""
    num_rooms: str = "0"
    extra_beds: str = "0"
    num_adults: str = "1"
    num_children: str = "0"
    room_type: Optional[str] = ""
    
    @validator('booking_number')
    def booking_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Booking number is required')
        return str(v).strip()
    
    @validator('guest_name')
    def guest_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Guest name is required')
        return str(v).strip()
    
    @validator('hotel_name')
    def hotel_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Hotel name is required')
        return str(v).strip()


class TourVoucherData(BaseModel):
    """Validation schema for tour voucher data."""
    booking_number: str = Field(..., min_length=1, max_length=50)
    guest_name: str = Field(..., min_length=1, max_length=100)
    guest_mobile_no: Optional[str] = ""
    tour_name: str = Field(..., min_length=1, max_length=200)
    package_name: Optional[str] = ""
    service_date: Optional[str] = ""
    pickup_from: Optional[str] = ""
    drop_to: Optional[str] = ""
    pick_time: Optional[str] = ""
    cancellation_policy: Optional[str] = ""
    book_payable_by: Optional[str] = ""
    num_adults: str = "1"
    num_children: str = "0"
    service_type: str = "Private Transfer"
    
    @validator('booking_number')
    def booking_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Booking number is required')
        return str(v).strip()
    
    @validator('guest_name')
    def guest_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Guest name is required')
        return str(v).strip()
    
    @validator('tour_name')
    def tour_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError('Tour name is required')
        return str(v).strip()


class BulkJobStatus(BaseModel):
    """Track status of bulk generation job."""
    job_id: str
    voucher_type: str  # 'hotel' or 'tour'
    total_rows: int
    processed_rows: int = 0
    successful: int = 0
    failed: int = 0
    status: str = 'pending'  # pending, processing, completed, failed
    errors: list = []
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    output_zip_filename: Optional[str] = None