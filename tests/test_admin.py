import pytest
from unittest.mock import patch
from handlers.admin import is_admin

@patch('handlers.admin.ADMIN_IDS', [123, 456])
def test_is_admin_true():
    """Test that is_admin returns True when the user ID is in ADMIN_IDS."""
    assert is_admin(123) is True
    assert is_admin(456) is True

@patch('handlers.admin.ADMIN_IDS', [123, 456])
def test_is_admin_false():
    """Test that is_admin returns False when the user ID is not in ADMIN_IDS."""
    assert is_admin(789) is False
    assert is_admin(0) is False
