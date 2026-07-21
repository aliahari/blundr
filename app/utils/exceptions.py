"""
Custom exceptions for the Chess Blunder Analyzer.
"""


class LichessAPIError(Exception):
    """Base exception for Lichess API errors."""
    
    def __init__(self, message: str, status_code: int = None, response_text: str = None):
        self.message = message
        self.status_code = status_code
        self.response_text = response_text
        super().__init__(self.message)


class RateLimitExceededError(LichessAPIError):
    """Exception raised when rate limit is exceeded."""
    
    def __init__(self, retry_after: int = None, message: str = None):
        self.retry_after = retry_after
        self.message = message or f"Rate limit exceeded. Retry after {retry_after} seconds."
        super().__init__(self.message, status_code=429)


class UserNotFoundError(LichessAPIError):
    """Exception raised when a user is not found."""
    
    def __init__(self, username: str):
        self.username = username
        self.message = f"User '{username}' not found on Lichess."
        super().__init__(self.message, status_code=404)


class GameNotFoundError(LichessAPIError):
    """Exception raised when a game is not found."""
    
    def __init__(self, game_id: str):
        self.game_id = game_id
        self.message = f"Game '{game_id}' not found."
        super().__init__(self.message, status_code=404)


class InvalidDateRangeError(ValueError):
    """Exception raised when date range is invalid."""
    
    def __init__(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date
        self.message = f"Invalid date range: {start_date} must be before {end_date}."
        super().__init__(self.message)


class NoGamesFoundError(Exception):
    """Exception raised when no games are found in the date range."""
    
    def __init__(self, username: str, start_date, end_date):
        self.username = username
        self.start_date = start_date
        self.end_date = end_date
        self.message = f"No games found for user '{username}' between {start_date} and {end_date}."
        super().__init__(self.message)


class PGNParseError(Exception):
    """Exception raised when PGN parsing fails."""
    
    def __init__(self, game_id: str, pgn: str, error: str):
        self.game_id = game_id
        self.pgn = pgn
        self.error = error
        self.message = f"Failed to parse PGN for game {game_id}: {error}"
        super().__init__(self.message)
