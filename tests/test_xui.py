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


def test_make_vless_link_not_found():
    """Test when the inbound ID is not found in the panel inbounds."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {"inbounds": {}}
    assert xui.make_vless_link(client_uuid, email, panel, 1) == ""


def test_make_vless_link_basic():
    """Test basic vless link generation (tcp, none)."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {
        "name": "MyServer",
        "inbounds": {
            "1": {
                "host": "myhost.com",
                "port": 443,
                "network": "tcp",
                "security": "none",
                "label": "tcp-inbound"
            }
        }
    }
    url = xui.make_vless_link(client_uuid, email, panel, 1)

    assert url.startswith("vless://test-uuid@myhost.com:443?")
    assert "type=tcp" in url
    assert "security=none" in url
    assert url.endswith("#test%40example.com-MyServer-tcp-inbound")


def test_make_vless_link_reality():
    """Test vless link generation with REALITY security."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {
        "name": "RealityServer",
        "inbounds": {
            "2": {
                "host": "reality.com",
                "port": 443,
                "network": "tcp",
                "security": "reality",
                "public_key": "pubkey123",
                "fingerprint": "firefox",
                "sni": "yahoo.com",
                "short_id": "sid123",
                "spiderX": "/spx",
                "flow": "xtls-rprx-vision"
            }
        }
    }
    url = xui.make_vless_link(client_uuid, email, panel, 2)

    assert "security=reality" in url
    assert "pbk=pubkey123" in url
    assert "fp=firefox" in url
    assert "sni=yahoo.com" in url
    assert "sid=sid123" in url
    assert "spx=%2Fspx" in url
    assert "flow=xtls-rprx-vision" in url


def test_make_vless_link_tls():
    """Test vless link generation with TLS security."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {
        "server_host": "fallback.com",  # Should use this since 'host' is missing
        "inbounds": {
            "3": {
                "port": 8443,
                "network": "tcp",
                "security": "tls",
                "sni": "cloudflare.com"
            }
        }
    }
    # Testing numeric inbound ID passing
    url = xui.make_vless_link(client_uuid, email, panel, 3)

    assert url.startswith("vless://test-uuid@fallback.com:8443?")
    assert "security=tls" in url
    assert "sni=cloudflare.com" in url


def test_make_vless_link_xhttp():
    """Test vless link generation with xhttp network."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {
        "inbounds": {
            "4": {
                "network": "xhttp",
                "security": "tls",
                "path": "/xhttp-path",
                "xhttp_mode": "multi",
                "ws_host": "ws.xhttp.com"
            }
        }
    }
    url = xui.make_vless_link(client_uuid, email, panel, 4)

    assert "type=xhttp" in url
    assert "path=%2Fxhttp-path" in url
    assert "mode=multi" in url
    assert "host=ws.xhttp.com" in url


def test_make_vless_link_grpc():
    """Test vless link generation with grpc network."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {
        "inbounds": {
            "5": {
                "network": "grpc",
                "security": "tls",
                "grpc_service": "my_grpc_service"
            }
        }
    }
    url = xui.make_vless_link(client_uuid, email, panel, 5)

    assert "type=grpc" in url
    assert "serviceName=my_grpc_service" in url
    assert "mode=gun" in url


def test_make_vless_link_ws():
    """Test vless link generation with ws network."""
    client_uuid = "test-uuid"
    email = "test@example.com"
    panel = {
        "inbounds": {
            "6": {
                "network": "ws",
                "security": "none",
                "path": "/ws-path",
                "ws_host": "ws.host.com"
            }
        }
    }
    url = xui.make_vless_link(client_uuid, email, panel, 6)

    assert "type=ws" in url
    assert "path=%2Fws-path" in url
    assert "host=ws.host.com" in url
