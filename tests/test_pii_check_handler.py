"""T46 검사 함수의 판정. 한 숫자 묶음은 한 종류로만 센다."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pii_check", Path(__file__).parents[1] / "functions/pii_check/pii_check.py")
pii_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pii_check)


@pytest.mark.parametrize("text,counts", [
    ("주민번호 900101-1234567 입니다", {"rrn": 1}),
    ("카드 1234-5678-9012-3456 로 결제", {"card": 1}),
    ("카드 1234 5678 9012 3456", {"card": 1}),
    ("계좌 110-123-456789 로 보내 주세요", {"account": 1}),
    ("카드 1234-5678-9012-3456, 계좌 110-123-456789", {"card": 1, "account": 1}),
    ("일반 상담 메시지 12", {}),
])
def test_each_number_is_counted_under_one_kind(text, counts):
    assert pii_check.detect(text) == counts
