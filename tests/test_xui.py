import pytest
from unittest.mock import patch
import xui

def test_make_sub_url():
    """Test that make_sub_url correctly formats the URL."""
    # Since SUB_BASE_URL is used directly from xui, we mock xui.SUB_BASE_URL
    with patch('xui.SUB_BASE_URL', 'http://mock-sub-url.com/sub'):
        sub_id = "test-sub-id-123"
        expected_url = "http://mock-sub-url.com/sub/test-sub-id-123"
        assert xui.make_sub_url(sub_id) == expected_url
