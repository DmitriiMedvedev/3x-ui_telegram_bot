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

def test_build_client_obj_str_inbound_id():
    panel = {
        "inbounds": {
            "1": {"flow": "xtls-rprx-vision"}
        }
    }
    client_uuid = "client-uuid-123"
    email = "test@example.com"
    sub_id = "sub-id-123"
    inbound_id = 1
    exp_ms = 1000000

    result = xui._build_client_obj(
        client_uuid=client_uuid,
        email=email,
        sub_id=sub_id,
        inbound_id=inbound_id,
        panel=panel,
        exp_ms=exp_ms,
    )
    assert result["id"] == client_uuid
    assert result["email"] == email
    assert result["enable"] is True
    assert result["flow"] == "xtls-rprx-vision"
    assert result["limitIp"] == 0
    assert result["totalGB"] == 0
    assert result["alterId"] == 0
    assert result["tgId"] == ""
    assert result["expiryTime"] == exp_ms
    assert result["subId"] == sub_id

def test_build_client_obj_int_inbound_id():
    panel = {
        "inbounds": {
            1: {"flow": "xtls-rprx-vision-int"}
        }
    }
    client_uuid = "client-uuid-123"
    email = "test@example.com"
    sub_id = "sub-id-123"
    inbound_id = 1
    exp_ms = 1000000

    result = xui._build_client_obj(
        client_uuid=client_uuid,
        email=email,
        sub_id=sub_id,
        inbound_id=inbound_id,
        panel=panel,
        exp_ms=exp_ms,
    )
    assert result["flow"] == "xtls-rprx-vision-int"

def test_build_client_obj_no_inbound_id():
    panel = {
        "inbounds": {
            "2": {"flow": "xtls-rprx-vision"}
        }
    }
    client_uuid = "client-uuid-123"
    email = "test@example.com"
    sub_id = "sub-id-123"
    inbound_id = 1
    exp_ms = 1000000

    result = xui._build_client_obj(
        client_uuid=client_uuid,
        email=email,
        sub_id=sub_id,
        inbound_id=inbound_id,
        panel=panel,
        exp_ms=exp_ms,
    )
    assert result["flow"] == ""

def test_build_client_obj_no_inbounds():
    panel = {}
    client_uuid = "client-uuid-123"
    email = "test@example.com"
    sub_id = "sub-id-123"
    inbound_id = 1
    exp_ms = 1000000

    result = xui._build_client_obj(
        client_uuid=client_uuid,
        email=email,
        sub_id=sub_id,
        inbound_id=inbound_id,
        panel=panel,
        exp_ms=exp_ms,
    )
    assert result["flow"] == ""
    assert result["id"] == client_uuid
    assert result["expiryTime"] == exp_ms
