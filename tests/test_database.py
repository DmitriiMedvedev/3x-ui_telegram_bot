import unittest
from database import _safe_json_list, _safe_json_dict

class TestSafeJsonUtils(unittest.TestCase):

    def test_safe_json_list_with_list(self):
        self.assertEqual(_safe_json_list(["1", "2"]), ["1", "2"])
        self.assertEqual(_safe_json_list([1, 2]), ["1", "2"])
        self.assertEqual(_safe_json_list([]), [])

    def test_safe_json_list_with_json_string(self):
        self.assertEqual(_safe_json_list('["1", "2"]'), ["1", "2"])
        self.assertEqual(_safe_json_list('[1, 2]'), ["1", "2"])
        self.assertEqual(_safe_json_list('[]'), [])

    def test_safe_json_list_with_falsy(self):
        self.assertEqual(_safe_json_list(None), [])
        self.assertEqual(_safe_json_list(""), [])

    def test_safe_json_list_with_invalid_json(self):
        self.assertEqual(_safe_json_list("invalid json"), [])

    def test_safe_json_list_with_non_list_json(self):
        self.assertEqual(_safe_json_list('{"key": "value"}'), [])
        self.assertEqual(_safe_json_list('123'), [])
        self.assertEqual(_safe_json_list('"string"'), [])

    def test_safe_json_dict_with_dict(self):
        self.assertEqual(_safe_json_dict({"a": 1, "b": "2"}), {"a": 1, "b": "2"})
        self.assertEqual(_safe_json_dict({1: "one"}), {"1": "one"})
        self.assertEqual(_safe_json_dict({}), {})

    def test_safe_json_dict_with_json_string(self):
        self.assertEqual(_safe_json_dict('{"a": 1, "b": "2"}'), {"a": 1, "b": "2"})
        self.assertEqual(_safe_json_dict('{}'), {})

    def test_safe_json_dict_with_falsy(self):
        self.assertEqual(_safe_json_dict(None), {})
        self.assertEqual(_safe_json_dict(""), {})

    def test_safe_json_dict_with_invalid_json(self):
        self.assertEqual(_safe_json_dict("invalid json"), {})

    def test_safe_json_dict_with_non_dict_json(self):
        self.assertEqual(_safe_json_dict('["a", "b"]'), {})
        self.assertEqual(_safe_json_dict('123'), {})
        self.assertEqual(_safe_json_dict('"string"'), {})

if __name__ == "__main__":
    unittest.main()
