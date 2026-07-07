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

def test_make_ss_link_happy_path():
    """Test successful generation of a shadowsocks link."""
    panel = {
        "name": "TestServer",
        "server_host": "192.168.1.1",
        "inbounds": {
            "1": {
                "protocol": "shadowsocks",
                "method": "aes-256-gcm",
                "password": "testpassword",
                "port": 8443
            }
        }
    }

    # expected base64 for "aes-256-gcm:testpassword"
    import base64
    from urllib.parse import quote
    expected_cred = base64.b64encode(b"aes-256-gcm:testpassword").decode()
    expected_label = quote("testuser-TestServer-SS")
    expected_link = f"ss://{expected_cred}@192.168.1.1:8443#{expected_label}"

    assert xui.make_ss_link("testuser", panel) == expected_link

def test_make_ss_link_ss_protocol():
    """Test successful generation of a shadowsocks link using 'ss' protocol."""
    panel = {
        "name": "TestServer2",
        "server_host": "192.168.1.2",
        "inbounds": {
            "2": {
                "protocol": "ss",
                "method": "chacha20-poly1305",
                "password": "anotherpassword",
                "port": 443
            }
        }
    }

    import base64
    from urllib.parse import quote
    expected_cred = base64.b64encode(b"chacha20-poly1305:anotherpassword").decode()
    expected_label = quote("user2-TestServer2-SS")
    expected_link = f"ss://{expected_cred}@192.168.1.2:443#{expected_label}"

    assert xui.make_ss_link("user2", panel) == expected_link

def test_make_ss_link_missing_password():
    """Test generation skips inbounds without a password."""
    panel = {
        "name": "TestServer3",
        "server_host": "192.168.1.3",
        "inbounds": {
            "3": {
                "protocol": "ss",
                "method": "chacha20-poly1305",
                # "password" is missing
                "port": 443
            }
        }
    }

    # Should skip the inbound with no password and return empty string
    assert xui.make_ss_link("user3", panel) == ""

def test_make_ss_link_missing_protocol():
    """Test generation skips inbounds with missing or unsupported protocol."""
    panel = {
        "name": "TestServer4",
        "server_host": "192.168.1.4",
        "inbounds": {
            "4": {
                "protocol": "vless", # Unsupported protocol for SS
                "method": "chacha20-poly1305",
                "password": "somepassword",
                "port": 443
            },
            "5": {
                # missing protocol
                "method": "chacha20-poly1305",
                "password": "somepassword",
                "port": 443
            }
        }
    }

    assert xui.make_ss_link("user4", panel) == ""

def test_make_ss_link_default_values():
    """Test generation uses default values when certain fields are missing."""
    panel = {
        "name": "TestServer5",
        # missing server_host
        "inbounds": {
            "6": {
                "protocol": "ss",
                # missing method
                "password": "defaultpassword",
                # missing port
            }
        }
    }

    import base64
    from urllib.parse import quote
    # Default method is "chacha20-poly1305"
    expected_cred = base64.b64encode(b"chacha20-poly1305:defaultpassword").decode()
    expected_label = quote("user5-TestServer5-SS")
    # Default host is "127.0.0.1", default port is 8388
    expected_link = f"ss://{expected_cred}@127.0.0.1:8388#{expected_label}"

    assert xui.make_ss_link("user5", panel) == expected_link
