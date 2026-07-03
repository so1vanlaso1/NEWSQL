"""Phase 12: previous-result -> ReviewSeed resolution (plan §8.2-8.3)."""
from backend.analysis import review_target_resolver as rtr
from backend.memory.models import ResultEntity, Turn


def _customer_turn(n=10) -> Turn:
    """A 'Top N khách hàng' result turn: display_rows + a top-row entity structure."""
    rows = [{"khach_hang_id": f"KH_{i:03d}", "ten_khach_hang": f"Cua hang {i}",
             "doanh_thu": 1000 - i} for i in range(1, n + 1)]
    return Turn(
        turn_id="t1", conversation_id="c1", needs_sql=True,
        generated_sql="SELECT kh.khach_hang_id, kh.ten_khach_hang, SUM(ct.thanh_tien) ...",
        user_question="Top 10 khách hàng theo doanh thu tháng 3/2025",
        standalone_question="Top 10 khách hàng theo doanh thu tháng 3/2025",
        selected_metrics=["doanh_thu"], selected_filters=["2025-03"],
        selected_tables=["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
        result_columns=["khach_hang_id", "ten_khach_hang", "doanh_thu"],
        result_preview=rows[:5], display_rows=rows,
        result_entities=[ResultEntity(type="khach_hang", id_column="khach_hang_id",
                                      id_value="KH_001", name_column="ten_khach_hang",
                                      name_value="Cua hang 1")])


def test_rank_one_resolves_to_top_row():
    seed = rtr.resolve("Phân tích sâu khách hàng top 1", [_customer_turn()])
    assert seed.ok
    assert seed.target_entity.rank == 1
    assert seed.target_entity.id_value == "KH_001"
    assert seed.target_entity.type == "khach_hang"
    assert seed.base_metrics == ["doanh_thu"]
    assert seed.base_tables == ["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"]
    assert seed.source_turn_id == "t1"


def test_explicit_rank_resolves_that_row():
    seed = rtr.resolve("Phân tích dòng 3", [_customer_turn()])
    assert seed.ok and seed.target_entity.rank == 3 and seed.target_entity.id_value == "KH_003"


def test_name_match_resolves_that_entity():
    seed = rtr.resolve("Phân tích sâu Cua hang 5", [_customer_turn()])
    assert seed.ok and seed.target_entity.id_value == "KH_005" and seed.target_entity.rank == 5


def test_name_match_is_token_boundary_not_substring():
    # "Cua hang 15" must NOT collide with the "Cua hang 1" row (a raw-substring bug).
    seed = rtr.resolve("Phân tích sâu Cua hang 15", [_customer_turn(20)])
    assert seed.ok and seed.target_entity.id_value == "KH_015" and seed.target_entity.rank == 15


def test_last_row_cue():
    seed = rtr.resolve("Phân tích thằng cuối cùng", [_customer_turn(10)])
    assert seed.ok and seed.target_entity.rank == 10 and seed.target_entity.id_value == "KH_010"


def test_bare_reference_defaults_to_top():
    seed = rtr.resolve("Phân tích cái này", [_customer_turn()])
    assert seed.ok and seed.target_entity.rank == 1


def test_entity_filter_sql():
    seed = rtr.resolve("Phân tích khách hàng top 1", [_customer_turn()])
    assert seed.entity_filter_sql() == "AND khach_hang_id = 'KH_001'"


def test_out_of_range_rank_refuses():
    seed = rtr.resolve("Phân tích dòng 50", [_customer_turn(10)])
    assert seed.ok is False and seed.reason


def test_no_previous_turn_refuses():
    seed = rtr.resolve("Phân tích top 1", [])
    assert seed.ok is False and seed.reason


def test_aggregate_result_without_entity_refuses():
    agg = Turn(turn_id="t2", conversation_id="c1", needs_sql=True, generated_sql="SELECT SUM(...)",
               user_question="Tổng doanh thu 2025", result_columns=["doanh_thu"],
               display_rows=[{"doanh_thu": 12345}])
    seed = rtr.resolve("Phân tích cái này", [agg])
    assert seed.ok is False and seed.reason


def test_rank_regex_ignores_entity_and_month_word_collisions():
    # "cửa hàng 30", "tháng 5", "khách hàng 5", "số 5" must NOT be misread as a rank
    # (the bare hang/so/thu regex-collision bug). Genuine rank cues still resolve.
    assert rtr._resolve_rank("phan tich cua hang 30", 40) is None
    assert rtr._resolve_rank("phan tich thang 5 nay", 12) is None
    assert rtr._resolve_rank("phan tich khach hang 5", 40) is None
    assert rtr._resolve_rank("phan tich so 5", 40) is None
    assert rtr._resolve_rank("dong 3", 10) == 3
    assert rtr._resolve_rank("top 1", 10) == 1
    assert rtr._resolve_rank("hang thu 4", 10) == 4
    assert rtr._resolve_rank("dong thu 2", 10) == 2
    assert rtr._resolve_rank("row 2", 10) == 2
    assert rtr._resolve_rank("xep hang 5", 10) == 5


def test_unlisted_name_defaults_to_top_not_spurious_rank():
    # Rows named "Diem ban N"; user paraphrases "cửa hàng 3" (not a row name, not a rank cue).
    # Must default to the top row, not misread "hang 3" as rank 3 (-> wrong entity).
    rows = [{"khach_hang_id": f"KH_{i:03d}", "ten_khach_hang": f"Diem ban {i}", "doanh_thu": 9 - i}
            for i in range(1, 6)]
    turn = Turn(turn_id="t", conversation_id="c", needs_sql=True, generated_sql="SELECT ...",
                user_question="Top 5 khách hàng",
                result_columns=["khach_hang_id", "ten_khach_hang", "doanh_thu"], display_rows=rows,
                result_entities=[ResultEntity(type="khach_hang", id_column="khach_hang_id",
                                              id_value="KH_001", name_column="ten_khach_hang",
                                              name_value="Diem ban 1")])
    seed = rtr.resolve("phân tích cửa hàng 3", [turn])
    assert seed.ok and seed.target_entity.rank == 1 and seed.target_entity.id_value == "KH_001"


def test_entity_columns_inferred_when_result_entities_missing():
    rows = [{"san_pham_id": f"SP_{i}", "ten_san_pham": f"SP {i}", "doanh_thu": 9 - i}
            for i in range(1, 4)]
    turn = Turn(turn_id="t3", conversation_id="c1", needs_sql=True, generated_sql="SELECT ...",
                user_question="Top sản phẩm", result_columns=["san_pham_id", "ten_san_pham", "doanh_thu"],
                display_rows=rows)  # note: no result_entities
    seed = rtr.resolve("Phân tích sản phẩm top 1", [turn])
    assert seed.ok and seed.target_entity.type == "san_pham" and seed.target_entity.id_value == "SP_1"
