import pytest
from database import _safe_json_dict

def test_safe_json_dict_with_dict():
    # Should convert keys to string
    data = {1: "a", "b": 2}
    result = _safe_json_dict(data)
    assert result == {"1": "a", "b": 2}

def test_safe_json_dict_with_valid_json_dict():
    # Should parse JSON dict
    data = '{"1": "a", "b": 2}'
    result = _safe_json_dict(data)
    assert result == {"1": "a", "b": 2}

def test_safe_json_dict_with_valid_json_list():
    # Should return empty dict if JSON is a list
    data = '["a", "b"]'
    result = _safe_json_dict(data)
    assert result == {}

def test_safe_json_dict_with_none():
    # Should handle None and return empty dict
    data = None
    result = _safe_json_dict(data)
    assert result == {}

def test_safe_json_dict_with_empty_string():
    # Should handle empty string and return empty dict
    data = ""
    result = _safe_json_dict(data)
    assert result == {}

def test_safe_json_dict_with_malformed_json():
    # Should catch JSONDecodeError and return empty dict
    data = "{invalid json"
    result = _safe_json_dict(data)
    assert result == {}

def test_safe_json_dict_with_unparsable_type():
    # Should catch TypeError if json.loads fails on type
    class Unparsable:
        pass
    data = Unparsable()
    result = _safe_json_dict(data)
    assert result == {}

def test_safe_json_dict_with_integer():
    # Integer will cause json.loads to fail (if not string/bytes/bytearray)
    # Actually json.loads(123) might work in some Python versions or fail, but `json.loads(123 or '{}')` -> 123 is true-ish, json.loads(123) -> TypeError
    data = 123
    result = _safe_json_dict(data)
    assert result == {}
