import pytest
from billing import fmt_bytes

@pytest.mark.parametrize("bytes_val, expected", [
    (0, "0.0 КБ"),
    (512, "0.5 КБ"),
    (1024, "1.0 КБ"),
    (1_048_575, "1024.0 КБ"),
    (1_048_576, "1.0 МБ"),
    (1_572_864, "1.5 МБ"),
    (1_073_741_823, "1024.0 МБ"),
    (1_073_741_824, "1.00 ГБ"),
    (1_610_612_736, "1.50 ГБ"),
])
def test_fmt_bytes(bytes_val: int, expected: str):
    assert fmt_bytes(bytes_val) == expected
