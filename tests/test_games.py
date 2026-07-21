"""
Tests for game-related functionality.

This module tests:
- Game fetching from Lichess
- Date validation
- Response formatting
- Error handling
"""
import pytest
from datetime import date

from app.models.schemas import GameRequest, GameType
from app.models.game import LichessGame, Player, Clock
from app.utils.date_utils import parse_date, date_to_timestamp, format_date_range
from app.utils.exceptions import (
    InvalidDateRangeError,
    UserNotFoundError,
    NoGamesFoundError
)

# The single shared TestClient — see conftest.py for why there is only one
from tests.conftest import client


class TestHealthCheck:
    """Tests for health check endpoints."""
    
    def test_health_check(self):
        """Test that health check endpoint returns healthy status."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "app_name" in data
    
    def test_readiness_check(self):
        """Test that readiness check endpoint returns ready status."""
        response = client.get("/api/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
    
    def test_root_endpoint(self):
        """Test root endpoint returns API info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "app_name" in data
        assert "version" in data
        assert "docs" in data


class TestDateUtilities:
    """Tests for date utility functions."""
    
    def test_parse_date_iso_format(self):
        """Test parsing ISO format date (YYYY-MM-DD)."""
        result = parse_date("2024-01-15")
        assert result == date(2024, 1, 15)
    
    def test_parse_date_slash_format(self):
        """Test parsing slash format date (YYYY/MM/DD)."""
        result = parse_date("2024/01/15")
        assert result == date(2024, 1, 15)
    
    def test_parse_date_us_format(self):
        """Test parsing US format date (MM/DD/YYYY)."""
        result = parse_date("01/15/2024")
        assert result == date(2024, 1, 15)
    
    def test_parse_date_european_format(self):
        """Test parsing European format date (DD-MM-YYYY)."""
        result = parse_date("15-01-2024")
        assert result == date(2024, 1, 15)
    
    def test_parse_date_invalid_format(self):
        """Test that invalid date format raises ValueError."""
        with pytest.raises(ValueError):
            parse_date("invalid-date")
    
    def test_parse_date_empty(self):
        """Test that empty date string raises ValueError."""
        with pytest.raises(ValueError):
            parse_date("")
    
    def test_date_to_timestamp(self):
        """Test converting date to timestamp."""
        d = date(2024, 1, 15)
        timestamp = date_to_timestamp(d)
        # Jan 15, 2024 00:00:00 UTC in milliseconds
        expected = 1705276800000
        assert timestamp == expected
    
    def test_date_to_timestamp_seconds(self):
        """Test converting date to timestamp in seconds."""
        d = date(2024, 1, 15)
        timestamp = date_to_timestamp(d, milliseconds=False)
        expected = 1705276800
        assert timestamp == expected
    
    def test_format_date_range(self):
        """Test formatting date range."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        result = format_date_range(start, end)
        assert result == "Jan 01, 2024 - Jan 31, 2024"


class TestGameModels:
    """Tests for game data models."""
    
    def test_player_creation(self):
        """Test creating a Player instance."""
        player = Player(
            id="user123",
            name="TestPlayer",
            rating=2000,
            provisional=False,
            color="white",
            result="win"
        )
        assert player.id == "user123"
        assert player.name == "TestPlayer"
        assert player.rating == 2000
        assert player.color == "white"
        assert player.result == "win"
    
    def test_lichess_game_creation(self):
        """Test creating a LichessGame instance."""
        white = Player(id="w1", name="White", rating=2000, color="white")
        black = Player(id="b1", name="Black", rating=1900, color="black")
        
        from datetime import datetime
        
        game = LichessGame(
            id="game123",
            created_at=datetime(2024, 1, 15, 12, 0, 0),
            white=white,
            black=black,
            result="1-0",
            end_status="checkmate",
            winner="white",
            rated=True
        )
        
        assert game.id == "game123"
        assert game.result == "1-0"
        assert game.end_status == "checkmate"
        assert game.winner == "white"
        assert game.rated is True
    
    def test_game_get_player_by_color(self):
        """Test getting player by color."""
        white = Player(id="w1", name="White", color="white")
        black = Player(id="b1", name="Black", color="black")
        
        from datetime import datetime
        
        game = LichessGame(
            id="game123",
            created_at=datetime(2024, 1, 15),
            white=white,
            black=black
        )
        
        assert game.get_player_by_color("white") == white
        assert game.get_player_by_color("black") == black
        assert game.get_player_by_color("invalid") is None
    
    def test_game_get_user_result(self):
        """Test getting user result from game."""
        white = Player(id="testuser", name="TestUser", rating=2000, result="win")
        black = Player(id="opponent", name="Opponent", rating=1900, result="loss")
        
        from datetime import datetime
        
        game = LichessGame(
            id="game123",
            created_at=datetime(2024, 1, 15),
            white=white,
            black=black,
            result="1-0"
        )
        
        assert game.get_user_game_result("TestUser") == "win"
        assert game.get_user_game_result("Opponent") == "loss"
        assert game.get_user_game_result("Unknown") is None
    
    def test_game_user_result_helpers(self):
        """Test user result helper methods."""
        white = Player(id="testuser", name="TestUser", result="win")
        black = Player(id="opponent", name="Opponent", result="loss")
        
        from datetime import datetime
        
        game = LichessGame(
            id="game123",
            created_at=datetime(2024, 1, 15),
            white=white,
            black=black,
            result="1-0"
        )
        
        assert game.is_user_winner("TestUser") is True
        assert game.is_user_winner("Opponent") is False
        assert game.is_user_loser("Opponent") is True
        assert game.is_user_drawer("TestUser") is False


class TestRequestValidation:
    """Tests for request validation."""
    
    def test_valid_game_request(self):
        """Test creating a valid GameRequest."""
        request = GameRequest(
            username="TestUser",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            max_games=50,
            game_type=GameType.RAPID
        )
        
        assert request.username == "TestUser"
        assert request.start_date == date(2024, 1, 1)
        assert request.end_date == date(2024, 1, 31)
        assert request.max_games == 50
        assert request.game_type == GameType.RAPID
    
    def test_game_request_defaults(self):
        """Test GameRequest with default values."""
        request = GameRequest(
            username="TestUser",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31)
        )
        
        assert request.max_games == 100
        assert request.game_type == GameType.ALL
    
    def test_game_request_username_validation(self):
        """Test username validation."""
        # Test valid usernames
        for username in ["a", "a" * 50, "ValidUser123", "user-name", "user_name"]:
            request = GameRequest(
                username=username,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31)
            )
            assert request.username == username
        
        # Test invalid usernames
        with pytest.raises(Exception):  # Pydantic validation error
            GameRequest(
                username="",  # Empty username
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31)
            )
        
        with pytest.raises(Exception):
            GameRequest(
                username="a" * 51,  # Username too long
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31)
            )
    
    def test_game_request_max_games_validation(self):
        """Test max_games validation."""
        # Valid values
        for max_games in [1, 100, 500, 1000]:
            request = GameRequest(
                username="TestUser",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                max_games=max_games
            )
            assert request.max_games == max_games
        
        # Invalid values
        with pytest.raises(Exception):
            GameRequest(
                username="TestUser",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                max_games=0  # Too small
            )
        
        with pytest.raises(Exception):
            GameRequest(
                username="TestUser",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                max_games=1001  # Too large
            )


class TestGameFetching:
    """Tests for game fetching endpoints."""
    
    def test_fetch_games_invalid_date_range(self):
        """Test fetching games with invalid date range."""
        request_data = {
            "username": "TestUser",
            "start_date": "2024-01-31",
            "end_date": "2024-01-01"  # End before start
        }
        
        response = client.post("/api/games/fetch", json=request_data)
        assert response.status_code == 400
        assert "Invalid date range" in response.json()["detail"]
    
    def test_fetch_games_valid_request_structure(self):
        """Test that valid request returns proper response structure."""
        # Use a known username that exists on Lichess
        request_data = {
            "username": "DrNykterstein",  # Popular streamer
            "start_date": "2024-01-01",
            "end_date": "2024-01-02",
            "max_games": 5
        }
        
        response = client.post("/api/games/fetch", json=request_data)
        
        # The response should be successful or return empty list
        if response.status_code == 200:
            data = response.json()
            assert "games" in data
            assert "total" in data
            assert "username" in data
            assert "date_range" in data
            assert isinstance(data["games"], list)
        else:
            # It's okay if there's an error (e.g., user doesn't exist)
            assert response.status_code in [404, 502, 429]
    
    def test_fetch_games_with_filters(self):
        """Test fetching games with filters."""
        request_data = {
            "username": "DrNykterstein",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "max_games": 10,
            "game_type": "rapid"
        }
        
        response = client.post("/api/games/fetch", json=request_data)
        
        # Should return successful response or appropriate error
        assert response.status_code in [200, 404, 502, 429]


class TestExceptions:
    """Tests for custom exceptions."""
    
    def test_invalid_date_range_exception(self):
        """Test InvalidDateRangeError exception."""
        start = date(2024, 1, 10)
        end = date(2024, 1, 1)
        
        with pytest.raises(InvalidDateRangeError) as exc_info:
            from app.utils.date_utils import validate_date_range
            validate_date_range(start, end)
        
        assert "2024-01-10" in str(exc_info.value.message)
        assert "2024-01-01" in str(exc_info.value.message)
    
    def test_user_not_found_exception(self):
        """Test UserNotFoundError exception."""
        error = UserNotFoundError("NonexistentUser")
        assert error.username == "NonexistentUser"
        assert "NonexistentUser" in error.message
        assert error.status_code == 404
    
    def test_no_games_found_exception(self):
        """Test NoGamesFoundError exception."""
        from datetime import date
        error = NoGamesFoundError("TestUser", date(2024, 1, 1), date(2024, 1, 31))
        assert error.username == "TestUser"
        assert "TestUser" in error.message
        assert "2024-01-01" in error.message
        assert "2024-01-31" in error.message
