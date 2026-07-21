"""
Date utility functions.
"""
from datetime import date, datetime, timezone
from typing import Optional, Tuple
import re


def parse_date(date_str: str) -> date:
    """
    Parse a date string into a date object.
    
    Supports formats:
    - YYYY-MM-DD (ISO format)
    - YYYY/MM/DD
    - MM/DD/YYYY
    - DD-MM-YYYY
    
    Args:
        date_str: The date string to parse
        
    Returns:
        A date object
        
    Raises:
        ValueError: If the date string cannot be parsed
    """
    if not date_str:
        raise ValueError("Date string cannot be empty")
    
    # Try ISO format first (YYYY-MM-DD)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        pass
    
    # Try YYYY/MM/DD
    try:
        return datetime.strptime(date_str, "%Y/%m/%d").date()
    except ValueError:
        pass
    
    # Try MM/DD/YYYY
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date()
    except ValueError:
        pass
    
    # Try DD-MM-YYYY
    try:
        return datetime.strptime(date_str, "%d-%m-%Y").date()
    except ValueError:
        pass
    
    raise ValueError(
        f"Invalid date format: '{date_str}'. "
        "Supported formats: YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY, DD-MM-YYYY"
    )


def date_to_timestamp(d: date, milliseconds: bool = True) -> int:
    """
    Convert a date to Unix timestamp (UTC).
    
    Args:
        d: The date to convert
        milliseconds: If True, return milliseconds; otherwise seconds
        
    Returns:
        Unix timestamp in milliseconds (default) or seconds
    """
    import calendar
    # Use UTC midnight for the date
    dt = datetime(d.year, d.month, d.day)
    timestamp = calendar.timegm(dt.timetuple())
    return timestamp * 1000 if milliseconds else timestamp


def timestamp_to_date(timestamp: int, milliseconds: bool = True) -> date:
    """
    Convert a Unix timestamp to a date.
    
    Args:
        timestamp: The Unix timestamp
        milliseconds: If True, timestamp is in milliseconds; otherwise seconds
        
    Returns:
        A date object
    """
    if milliseconds:
        timestamp = timestamp // 1000
    # UTC to mirror date_to_timestamp's use of calendar.timegm (UTC); using
    # the local timezone here would shift the date near local midnight
    # depending on the server's timezone.
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.date()


def format_date_range(start_date: date, end_date: date) -> str:
    """
    Format a date range as a human-readable string.
    
    Args:
        start_date: The start date
        end_date: The end date
        
    Returns:
        A formatted string like "Jan 1, 2024 - Jan 31, 2024"
    """
    start_str = start_date.strftime("%b %d, %Y")
    end_str = end_date.strftime("%b %d, %Y")
    return f"{start_str} - {end_str}"


def validate_date_range(start_date: date, end_date: date) -> None:
    """
    Validate that start_date is before or equal to end_date.
    
    Args:
        start_date: The start date
        end_date: The end date
        
    Raises:
        InvalidDateRangeError: If start_date is after end_date
    """
    if start_date > end_date:
        from .exceptions import InvalidDateRangeError
        raise InvalidDateRangeError(start_date, end_date)


def get_date_range_days(start_date: date, end_date: date) -> int:
    """
    Calculate the number of days between two dates.
    
    Args:
        start_date: The start date
        end_date: The end date
        
    Returns:
        The number of days (inclusive)
    """
    delta = end_date - start_date
    return abs(delta.days) + 1


def is_recent_date(d: date, days: int = 30) -> bool:
    """
    Check if a date is recent (within the last N days).
    
    Args:
        d: The date to check
        days: Number of days to consider as "recent"
        
    Returns:
        True if the date is recent, False otherwise
    """
    today = date.today()
    delta = today - d
    return delta.days <= days
