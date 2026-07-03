"""Phase 13: deterministic date-window resolution (plan §13.4)."""
from backend.analysis import date_window
from backend.analysis.models import ReviewSeed

_MIN, _MAX = "2024-01-01", "2025-06-24"


def _w(q, seed=None):
    return date_window.resolve_window(q, seed, _MIN, _MAX)


def test_month_slash_year():
    w = _w("Phân tích vì sao doanh thu tháng 3/2025 giảm?")
    assert (w.date_from, w.date_to) == ("2025-03-01", "2025-03-31")
    assert (w.compare_from, w.compare_to) == ("2025-02-01", "2025-02-28")
    assert w.label == "2025-03" and w.compare_label == "2025-02"


def test_month_space_year_without_nam():
    w = _w("Phân tích doanh thu tháng 5 2025")
    assert (w.date_from, w.date_to) == ("2025-05-01", "2025-05-31")
    assert (w.compare_from, w.compare_to) == ("2025-04-01", "2025-04-30")


def test_month_nam_year():
    w = _w("doanh thu tháng 5 năm 2025")
    assert w.label == "2025-05"


def test_month_only_defaults_to_latest_available_year():
    assert _w("Phân tích doanh thu tháng 5").label == "2025-05"     # 2025-05 <= data max
    assert _w("Phân tích doanh thu tháng 8").label == "2024-08"     # 2025-08 > data max -> 2024


def test_quarter():
    w = _w("Phân tích doanh thu quý 1 2025")
    assert (w.date_from, w.date_to) == ("2025-01-01", "2025-03-31")
    assert (w.compare_from, w.compare_to) == ("2024-10-01", "2024-12-31")


def test_year():
    w = _w("Phân tích doanh thu năm 2024")
    assert (w.date_from, w.date_to) == ("2024-01-01", "2024-12-31")
    assert (w.compare_from, w.compare_to) == ("2023-01-01", "2023-12-31")


def test_default_is_last_full_month():
    # data max 2025-06-24 is mid-month -> last *full* month is May 2025.
    w = _w("Phân tích vì sao doanh thu giảm")
    assert w.label == "2025-05" and w.compare_label == "2025-04"


def test_seed_filter_period_used_when_no_period_in_question():
    seed = ReviewSeed(ok=True, base_filters=["2025-02"])
    w = _w("phân tích sâu khách hàng này", seed)
    assert w.label == "2025-02"
