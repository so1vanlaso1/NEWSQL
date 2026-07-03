"""Phase 14: result profiler (plan §15.1). Fixture rows -> expected profiles."""
from backend.analysis import profiler


def test_pct_change_guards_zero_and_none():
    assert profiler.pct_change(120, 100) == 20.0
    assert profiler.pct_change(80, 100) == -20.0
    assert profiler.pct_change(10, 0) is None
    assert profiler.pct_change(10, None) is None


def test_kpi_profile_labelled_rows():
    cols = ["ky", "gia_tri"]
    rows = [{"ky": "ky_nay", "gia_tri": 820}, {"ky": "ky_truoc", "gia_tri": 1040}]
    p = profiler.profile("kpi", cols, rows)
    assert p["current"] == 820 and p["previous"] == 1040
    assert p["absolute_change"] == -220
    assert p["pct_change"] == -21.15
    assert p["trend"] == "down"


def test_kpi_zero_previous_flags_warning():
    rows = [{"ky": "ky_nay", "gia_tri": 500}, {"ky": "ky_truoc", "gia_tri": 0}]
    p = profiler.profile("kpi", ["ky", "gia_tri"], rows)
    assert p["pct_change"] is None
    assert "no_previous_baseline" in p["warnings"]


def test_by_dimension_contributors_and_concentration():
    cols = ["nhom", "ky_nay", "ky_truoc"]
    rows = [{"nhom": "A", "ky_nay": 100, "ky_truoc": 200},
            {"nhom": "B", "ky_nay": 300, "ky_truoc": 250}]
    p = profiler.profile("by_dimension", cols, rows)
    assert p["total_current"] == 400 and p["total_previous"] == 450
    assert p["total_change"] == -50
    assert p["biggest_mover"]["label"] == "A"
    assert p["top_negative"][0]["label"] == "A"
    assert p["top_positive"][0]["label"] == "B"
    assert p["top3_concentration"] == 1.0
    assert p["leader_share"] == 0.75


def test_by_dimension_single_period_ranks():
    cols = ["nhom", "san_luong"]
    rows = [{"nhom": "A", "san_luong": 30}, {"nhom": "B", "san_luong": 70}]
    p = profiler.profile("by_dimension", cols, rows)
    assert p["previous_field"] == ""
    assert p["leader_share"] == 0.7


def test_trend_direction_and_extrema():
    cols = ["thang", "gia_tri"]
    rows = [{"thang": "2025-01", "gia_tri": 100},
            {"thang": "2025-02", "gia_tri": 300},
            {"thang": "2025-03", "gia_tri": 80}]
    p = profiler.profile("trend", cols, rows)
    assert p["direction"] == "down"          # last(80) < first(100)
    assert p["best_period"]["period"] == "2025-02"
    assert p["worst_period"]["period"] == "2025-03"
    assert p["absolute_change"] == -20


def test_top_n_leader_share_and_gap():
    cols = ["ten", "gia_tri"]
    rows = [{"ten": "X", "gia_tri": 600}, {"ten": "Y", "gia_tri": 300},
            {"ten": "Z", "gia_tri": 100}]
    p = profiler.profile("top_n", cols, rows)
    assert p["leader"] == "X"
    assert p["leader_value"] == 600
    assert p["gap_to_second"] == 300
    assert p["leader_share"] == 0.6


def test_empty_result_warns():
    p = profiler.profile("kpi", ["ky", "gia_tri"], [])
    assert "empty_result" in p["warnings"]


def test_all_null_value_column_warns():
    rows = [{"ky": "ky_nay", "gia_tri": None}, {"ky": "ky_truoc", "gia_tri": None}]
    p = profiler.profile("kpi", ["ky", "gia_tri"], rows)
    assert "all_null_or_non_numeric" in p["warnings"]


def test_kpi_missing_current_value_warns():
    # Current period has no value but the previous does -> flag the missing number.
    rows = [{"ky": "ky_nay", "gia_tri": None}, {"ky": "ky_truoc", "gia_tri": 1040}]
    p = profiler.profile("kpi", ["ky", "gia_tri"], rows)
    assert p["current"] is None and p["previous"] == 1040
    assert "missing_current_value" in p["warnings"]


def test_top_n_zero_total_warns():
    rows = [{"ten": "X", "gia_tri": 0}, {"ten": "Y", "gia_tri": 0}]
    p = profiler.profile("top_n", ["ten", "gia_tri"], rows)
    assert p["leader_share"] is None
    assert "zero_total" in p["warnings"]


def test_by_dimension_all_flat_warns():
    # Every group unchanged between periods -> concentration undefined, flag "no_change".
    rows = [{"nhom": "A", "ky_nay": 100, "ky_truoc": 100},
            {"nhom": "B", "ky_nay": 200, "ky_truoc": 200}]
    p = profiler.profile("by_dimension", ["nhom", "ky_nay", "ky_truoc"], rows)
    assert p["top3_concentration"] is None
    assert "no_change" in p["warnings"]
