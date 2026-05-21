"""Bulk voucher generation with Excel/CSV support - Production Ready with Real-time Updates."""
import asyncio
import io
import logging
import os
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Callable

import pandas as pd

from .generator import generate_hotel_pdf, generate_tour_pdf

log = logging.getLogger("vikram.voucher.bulk")

# Store active jobs in memory
_job_store: Dict[str, 'BulkJob'] = {}

# Configuration
MAX_ROWS_PER_FILE = 500
SUPPORTED_EXTENSIONS = {'.xlsx', '.xls', '.csv'}


@dataclass
class BulkJob:
    """Represents a bulk generation job with complete tracking."""
    job_id: str
    voucher_type: str
    total_rows: int
    processed_rows: int = 0
    successful: int = 0
    failed: int = 0
    status: str = 'pending'
    errors: List[Dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    output_zip_path: Optional[str] = None
    output_zip_filename: Optional[str] = None
    results: List[Dict] = field(default_factory=list)
    file_name: Optional[str] = None
    
    def update(self):
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now()
    
    def to_dict(self) -> Dict:
        """Convert job to dictionary for API responses."""
        return {
            'job_id': self.job_id,
            'voucher_type': self.voucher_type,
            'total_rows': self.total_rows,
            'processed_rows': self.processed_rows,
            'successful': self.successful,
            'failed': self.failed,
            'status': self.status,
            'errors': self.errors[-20:],
            'file_name': self.file_name,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'progress_percent': self.get_progress_percent(),
        }
    
    def get_progress_percent(self) -> float:
        """Calculate progress percentage."""
        if self.total_rows == 0:
            return 0.0
        return round((self.processed_rows / self.total_rows) * 100, 1)


class BulkVoucherProcessor:
    """Production-ready bulk voucher processor with real-time progress updates."""
    
    HOTEL_REQUIRED_COLUMNS = ['booking_number', 'guest_name', 'hotel_name']
    TOUR_REQUIRED_COLUMNS = ['booking_number', 'guest_name', 'tour_name']
    
    HOTEL_COLUMN_MAPPING = {
        'booking_number': 'booking_number',
        'hotel_cfn': 'hotel_cfn',
        'guest_name': 'guest_name',
        'country': 'country',
        'hotel_name': 'hotel_name',
        'address': 'address',
        'contact_number': 'contact_number',
        'cancellation_policy': 'cancellation_policy',
        'check_in': 'check_in',
        'check_out': 'check_out',
        'book_payable_by': 'book_payable_by',
        'remarks': 'remarks',
        'num_rooms': 'num_rooms',
        'extra_beds': 'extra_beds',
        'num_adults': 'num_adults',
        'num_children': 'num_children',
        'room_type': 'room_type',
    }
    
    TOUR_COLUMN_MAPPING = {
        'booking_number': 'booking_number',
        'guest_name': 'guest_name',
        'guest_mobile_no': 'guest_mobile_no',
        'tour_name': 'tour_name',
        'package_name': 'package_name',
        'service_date': 'service_date',
        'pickup_from': 'pickup_from',
        'drop_to': 'drop_to',
        'pick_time': 'pick_time',
        'cancellation_policy': 'cancellation_policy',
        'book_payable_by': 'book_payable_by',
        'num_adults': 'num_adults',
        'num_children': 'num_children',
        'service_type': 'service_type',
    }
    
    HOTEL_DEFAULTS = {
        'num_rooms': '1',
        'extra_beds': '0',
        'num_adults': '2',
        'num_children': '0',
        'room_type': 'Standard',
        'hotel_cfn': '',
        'country': '',
        'address': '',
        'contact_number': '',
        'cancellation_policy': 'Standard cancellation policy applies',
        'check_in': '',
        'check_out': '',
        'book_payable_by': 'Guest',
        'remarks': '',
    }
    
    TOUR_DEFAULTS = {
        'num_adults': '2',
        'num_children': '0',
        'service_type': 'Private Transfer',
        'guest_mobile_no': '',
        'package_name': '',
        'service_date': '',
        'pickup_from': '',
        'drop_to': '',
        'pick_time': '',
        'cancellation_policy': 'Standard cancellation policy applies',
        'book_payable_by': 'Guest',
    }
    
    @classmethod
    def parse_excel_file(cls, file_content: bytes, filename: str, voucher_type: str) -> Tuple[Optional[pd.DataFrame], List[str], int]:
        """Parse uploaded Excel/CSV file content into DataFrame."""
        errors = []
        
        try:
            file_bytes = io.BytesIO(file_content)
            
            if filename.endswith('.csv'):
                df = pd.read_csv(file_bytes, encoding='utf-8')
                if df is None or df.empty:
                    file_bytes.seek(0)
                    df = pd.read_csv(file_bytes, encoding='latin1')
            else:
                df = pd.read_excel(file_bytes, engine='openpyxl')
            
            if df.empty:
                return None, ["Excel file is empty"], 0
            
            # Clean column names
            df.columns = df.columns.str.strip().str.lower()
            
            # Validate required columns
            required_cols = cls.HOTEL_REQUIRED_COLUMNS if voucher_type == 'hotel' else cls.TOUR_REQUIRED_COLUMNS
            missing_cols = [col for col in required_cols if col not in df.columns]
            
            if missing_cols:
                return None, [f"Missing required columns: {', '.join(missing_cols)}"], 0
            
            # Check row limit
            if len(df) > MAX_ROWS_PER_FILE:
                return None, [f"Too many rows. Maximum: {MAX_ROWS_PER_FILE}, Found: {len(df)}"], 0
            
            return df, errors, len(df)
            
        except Exception as e:
            log.exception("Failed to parse Excel file")
            return None, [f"Failed to parse file: {str(e)}"], 0
    
    @classmethod
    def row_to_hotel_dict(cls, row: pd.Series) -> Dict[str, Any]:
        """Convert DataFrame row to hotel voucher dict."""
        result = {}
        
        for form_field, excel_col in cls.HOTEL_COLUMN_MAPPING.items():
            if excel_col in row.index and pd.notna(row[excel_col]):
                result[form_field] = str(row[excel_col])
            else:
                result[form_field] = cls.HOTEL_DEFAULTS.get(form_field, '')
        
        # Ensure required fields have values
        result['booking_number'] = str(result.get('booking_number', '')).strip()
        result['guest_name'] = str(result.get('guest_name', '')).strip()
        result['hotel_name'] = str(result.get('hotel_name', '')).strip()
        
        return result
    
    @classmethod
    def row_to_tour_dict(cls, row: pd.Series) -> Dict[str, Any]:
        """Convert DataFrame row to tour voucher dict."""
        result = {}
        
        for form_field, excel_col in cls.TOUR_COLUMN_MAPPING.items():
            if excel_col in row.index and pd.notna(row[excel_col]):
                result[form_field] = str(row[excel_col])
            else:
                result[form_field] = cls.TOUR_DEFAULTS.get(form_field, '')
        
        # Ensure required fields have values
        result['booking_number'] = str(result.get('booking_number', '')).strip()
        result['guest_name'] = str(result.get('guest_name', '')).strip()
        result['tour_name'] = str(result.get('tour_name', '')).strip()
        
        return result


_processor = BulkVoucherProcessor()


def get_job_status(job_id: str) -> Optional[Dict]:
    """Get status of a bulk generation job."""
    job = _job_store.get(job_id)
    if not job:
        return None
    return job.to_dict()


async def process_bulk_upload(
    file_content: bytes,
    filename: str,
    voucher_type: str,
    job_id: str,
    on_progress: Optional[Callable] = None
) -> Tuple[bytes, str]:
    """
    Main entry point for bulk processing with real-time progress updates.
    """
    # Parse Excel file
    df, errors, row_count = _processor.parse_excel_file(file_content, filename, voucher_type)
    
    if df is None:
        raise ValueError(f"Failed to parse file: {'; '.join(errors)}")
    
    # Update job with total rows and set to processing
    job = _job_store.get(job_id)
    if job:
        job.total_rows = row_count
        job.status = 'processing'
        job.update()
        log.info(f"Job {job_id}: Starting processing of {row_count} rows")
    
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for idx, (_, row) in enumerate(df.iterrows(), 1):
            try:
                if voucher_type == 'hotel':
                    form_data = _processor.row_to_hotel_dict(row)
                    
                    booking_num = form_data.get('booking_number', '').strip()
                    guest = form_data.get('guest_name', '').strip()
                    hotel = form_data.get('hotel_name', '').strip()
                    
                    if not booking_num or not guest or not hotel:
                        raise ValueError(f"Missing required fields: booking='{booking_num}', guest='{guest}', hotel='{hotel}'")
                    
                    pdf_bytes, pdf_filename = generate_hotel_pdf(form_data)
                    
                else:
                    form_data = _processor.row_to_tour_dict(row)
                    
                    booking_num = form_data.get('booking_number', '').strip()
                    guest = form_data.get('guest_name', '').strip()
                    tour = form_data.get('tour_name', '').strip()
                    
                    if not booking_num or not guest or not tour:
                        raise ValueError(f"Missing required fields: booking='{booking_num}', guest='{guest}', tour='{tour}'")
                    
                    pdf_bytes, pdf_filename = generate_tour_pdf(form_data)
                
                safe_filename = pdf_filename.replace('/', '_').replace('\\', '_').replace(':', '_')
                zip_file.writestr(safe_filename, pdf_bytes)
                
                # Update job progress AFTER each successful row
                job = _job_store.get(job_id)
                if job:
                    job.processed_rows = idx
                    job.successful += 1
                    job.update()  # This updates updated_at timestamp
                    
                    # Log progress every row
                    log.info(f"Job {job_id}: Progress {idx}/{row_count} ({job.get_progress_percent()}%) - {safe_filename}")
                
                # Call progress callback if provided (for SSE)
                if on_progress:
                    try:
                        if asyncio.iscoroutinefunction(on_progress):
                            await on_progress(idx, row_count, job.successful if job else 0, job.failed if job else 0)
                        else:
                            on_progress(idx, row_count, job.successful if job else 0, job.failed if job else 0)
                    except Exception as e:
                        log.warning(f"Progress callback failed: {e}")
                
                # Small delay to allow SSE to send updates
                await asyncio.sleep(0.01)
                
            except Exception as e:
                log.error(f"Job {job_id}: Failed to generate voucher for row {idx}: {str(e)}")
                
                job = _job_store.get(job_id)
                if job:
                    job.processed_rows = idx
                    job.failed += 1
                    job.errors.append({
                        'row': idx,
                        'error': str(e),
                        'booking': row.get('booking_number', 'N/A') if hasattr(row, 'get') else 'N/A',
                    })
                    job.update()
    
    # Finalize job
    job = _job_store.get(job_id)
    if job:
        job.status = 'completed' if job.failed < job.total_rows else 'failed'
        job.completed_at = datetime.now()
        job.update()
    
    zip_buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"Vouchers_{voucher_type}_{timestamp}.zip"
    
    log.info(f"Job {job_id} completed: {job.successful if job else 0} successful, {job.failed if job else 0} failed")
    
    return zip_buffer.getvalue(), zip_filename


def save_zip_to_temp(zip_bytes: bytes, zip_filename: str) -> str:
    """Save ZIP file to temporary storage."""
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, zip_filename)
    
    with open(temp_path, 'wb') as f:
        f.write(zip_bytes)
    
    log.info(f"ZIP file saved to: {temp_path}")
    return temp_path


def warmup_bulk_processor():
    """Pre-warm the bulk processor."""
    log.info("Bulk processor initialized")