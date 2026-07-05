"""Phase 12: the heuristic 4-mode router (backend/analysis/mode_detector.py, plan §3)."""
import pytest

from backend.analysis import mode_detector as md
from backend.analysis.mode_detector import (
    ANALYTIC_FOLLOWUP,
    ANALYTIC_FROM_PREVIOUS_RESULT,
    ANALYTIC_MODE,
    GEO_PROSPECT,
    NORMAL_SQL,
    detect_mode,
)
from backend.memory.models import ResultEntity, Turn


def _sql_turn() -> Turn:
    """A minimal previous SQL turn (so last_sql_turn resolves)."""
    return Turn(
        turn_id="t1", conversation_id="c1", needs_sql=True,
        generated_sql="SELECT 1", user_question="Top 10 khách hàng theo doanh thu",
        result_columns=["khach_hang_id", "ten_khach_hang", "doanh_thu"],
        result_entities=[ResultEntity(type="khach_hang", id_column="khach_hang_id",
                                      id_value="KH_030", name_column="ten_khach_hang",
                                      name_value="Cua hang 30")])


class _Review:
    """Stand-in for a stored review (Phase 14); carries the latest-artifact flag."""
    def __init__(self, is_latest_artifact=True):
        self.is_latest_artifact = is_latest_artifact


# (message, has_prev_sql_turn, expected_mode)
_NORMAL = [
    ("Top 10 khách hàng theo doanh thu tháng 3/2025", False, NORMAL_SQL),
    ("5 sản phẩm bán chạy nhất 2025", False, NORMAL_SQL),
    ("Doanh thu tháng 5 là bao nhiêu", False, NORMAL_SQL),
    ("Liệt kê nhà phân phối miền Bắc", False, NORMAL_SQL),
    ("Số đơn hàng hôm nay", False, NORMAL_SQL),
    ("Câu SQL vừa rồi là gì", True, NORMAL_SQL),          # ask-about-sql, not analytic
    ("Sắp xếp theo doanh thu giảm dần", True, NORMAL_SQL),  # "giảm dần" is not a trigger
    ("Doanh thu theo tháng năm 2025", False, NORMAL_SQL),
    ("Bao nhiêu khách hàng ở Hà Nội", False, NORMAL_SQL),
    ("Danh sách sản phẩm ngừng bán", False, NORMAL_SQL),
    ("", False, NORMAL_SQL),
]

_ANALYTIC = [
    ("Vì sao doanh thu tháng 3 giảm?", False, ANALYTIC_MODE),
    ("Phân tích doanh thu tháng 5", False, ANALYTIC_MODE),
    ("Đánh giá hiệu suất ngành hàng", False, ANALYTIC_MODE),
    ("Tại sao doanh số giảm mạnh", False, ANALYTIC_MODE),
    ("Tìm nguyên nhân miền Trung yếu", False, ANALYTIC_MODE),
    ("Đề xuất cải thiện doanh thu", False, ANALYTIC_MODE),
    ("Analyze revenue trends", False, ANALYTIC_MODE),
    ("Why did sales drop", False, ANALYTIC_MODE),
    ("What caused the decline", False, ANALYTIC_MODE),
    ("How to improve customer retention", False, ANALYTIC_MODE),
    ("Phân tích top 10 khách hàng", False, ANALYTIC_MODE),   # over-trigger (downgrade later)
    ("Diagnose the revenue decline", False, ANALYTIC_MODE),
    ("Insight về doanh thu quý 2", False, ANALYTIC_MODE),
    # analytic + prev-ref but NO previous SQL turn -> fresh analysis
    ("Phân tích sâu khách hàng top 1", False, ANALYTIC_MODE),
]

_FROM_PREV = [
    ("Phân tích sâu khách hàng top 1", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Phân tích cái này", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Đánh giá công ty này", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Vì sao khách hàng này giảm", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Phân tích dòng đầu", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Analyze the top customer", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Phân tích sâu sản phẩm này", True, ANALYTIC_FROM_PREVIOUS_RESULT),
    ("Vì sao dòng 1 cao nhất", True, ANALYTIC_FROM_PREVIOUS_RESULT),
]


# Geo prospecting (Phase 19): distinctive "find nearby stores" asks.
_GEO = [
    ("Tìm cửa hàng tiềm năng quanh Quận 7 bán kính 800m", False, GEO_PROSPECT),
    ("Tìm cửa hàng tiềm năng quanh khách hàng KH_005", False, GEO_PROSPECT),
    ("Cửa hàng tiềm năng quanh tuyến của nhân viên NV_003", False, GEO_PROSPECT),
    ("Quanh khu vực này còn cửa hàng nào chưa là khách hàng không", False, GEO_PROSPECT),
    ("Mời hàng thêm quanh khu vực này", False, GEO_PROSPECT),
    # geo trigger wins even with an analytic word present
    ("Phân tích cửa hàng tiềm năng quanh Quận 7", True, GEO_PROSPECT),
]


@pytest.mark.parametrize("msg,has_prev,expected", _NORMAL + _ANALYTIC + _FROM_PREV + _GEO)
def test_detect_mode_cases(msg, has_prev, expected):
    turns = [_sql_turn()] if has_prev else []
    assert detect_mode(msg, turns) == expected, msg


def test_geo_does_not_hijack_plain_analytic():
    # A plain revenue analysis with no geo phrase stays ANALYTIC_MODE.
    assert detect_mode("Phân tích doanh thu tháng 5 theo ngành hàng", []) == ANALYTIC_MODE


def test_case_count_is_at_least_30():
    assert len(_NORMAL + _ANALYTIC + _FROM_PREV) >= 30


# ---- follow-up window (needs a review to exist) -----------------------------
def test_review_owns_followup_markers():
    turns = [_sql_turn()]
    assert detect_mode("Cho xem SQL đã dùng", turns, last_review=_Review()) == ANALYTIC_FOLLOWUP
    assert detect_mode("Phân tích tiếp", turns, last_review=_Review()) == ANALYTIC_FOLLOWUP
    assert detect_mode("Vẽ lại biểu đồ", turns, last_review=_Review()) == ANALYTIC_FOLLOWUP


def test_review_owns_referential_when_latest_artifact():
    # A referential question after a review (which is the latest artifact) -> FOLLOWUP.
    assert detect_mode("Cái này thì sao", [_sql_turn()], last_review=_Review(True)) == ANALYTIC_FOLLOWUP


def test_review_does_not_hijack_unrelated_question():
    # A fresh, non-referential question after a review still routes normally.
    assert detect_mode("Doanh thu tháng 5", [_sql_turn()], last_review=_Review()) == NORMAL_SQL


def test_followup_marker_without_review_is_not_followup():
    # No review yet -> the FOLLOWUP branch is dormant (Phase 12 reality).
    assert detect_mode("Cho xem SQL đã dùng", [_sql_turn()], last_review=None) == NORMAL_SQL


def test_thang_nay_stays_fresh_analysis():
    # "tháng này" / "tháng đầu" are calendar phrases, not previous-result references, so an
    # analytic question about them is a fresh ANALYTIC_MODE analysis even with a prior turn.
    assert detect_mode("Phân tích doanh thu tháng này theo ngành hàng", [_sql_turn()]) == ANALYTIC_MODE
    assert detect_mode("Phân tích doanh thu tháng đầu năm 2025", [_sql_turn()]) == ANALYTIC_MODE
    assert detect_mode("Doanh thu tháng này", [_sql_turn()]) == NORMAL_SQL  # no analytic trigger


def test_top_1_vs_top_10_boundary():
    # "top 1" is a previous-result reference; "top 10" is not.
    assert md.contains_any(md.normalize_vietnamese_text("top 1 khach hang"),
                           md.PREVIOUS_RESULT_REFERENCES) is True
    assert md.contains_any(md.normalize_vietnamese_text("top 10 khach hang"),
                           md.PREVIOUS_RESULT_REFERENCES) is False
