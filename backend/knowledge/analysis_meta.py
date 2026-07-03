"""Curated analytic knowledge for the FMCG sales DB (Phase 11, plan §10.4).

The hand-authored analytic layer, sibling to ``business_meta.py``. Where business_meta
holds schema/metric/join facts, this module holds the *investigation* knowledge that the
analytic pipeline (Phases 13-15) consumes:

- ``PLAYBOOKS``          diagnostic step packs (revenue_drop, top_customer, ...)
- ``DIMENSIONS``         grouping dimensions (category, customer, region, ...)
- ``CHART_RULES``        shape -> chart-type policy (one per shape)
- ``METRIC_EXTENSIONS``  analytic fields merged onto existing metric entries (§10.2)
- ``caveats(min, max)``  data-limitation notes (parameterized by the data window)

Everything here is seeded as ordinary ``knowledge.db`` entries via ``seed.seed_analysis``:
the owner edits or replaces every one of them in the UI, with the same embed-on-save +
hot-reload as schema knowledge.

SQL is SQLite. ``sql_hint`` templates use placeholders substituted by the planner /
fallback pack (plan §13.4): ``{date_from} {date_to} {compare_from} {compare_to}`` (date
literals) and ``{entity_filter}`` (a trailing WHERE fragment, empty for a fresh analysis).
Every hint parses as valid SQLite once the placeholders are filled.
"""
from __future__ import annotations

# ---- reusable SQL hint templates (all parse after placeholder substitution) -----
# A KPI compares one aggregate across two periods (this vs previous) as two labelled rows.
def _kpi_hint(agg: str, joins: str = "") -> str:
    return (
        "SELECT 'ky_nay' AS ky, {agg} AS gia_tri\n"
        "FROM don_hang_ban dh{joins}\n"
        "WHERE dh.trang_thai = 'NORMAL' "
        "AND dh.ngay_dat_hang BETWEEN '{{date_from}}' AND '{{date_to}}' {{entity_filter}}\n"
        "UNION ALL\n"
        "SELECT 'ky_truoc' AS ky, {agg} AS gia_tri\n"
        "FROM don_hang_ban dh{joins}\n"
        "WHERE dh.trang_thai = 'NORMAL' "
        "AND dh.ngay_dat_hang BETWEEN '{{compare_from}}' AND '{{compare_to}}' {{entity_filter}}"
    ).format(agg=agg, joins=joins)


_JOIN_LINE = "\nJOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id"

# KPI: net revenue this vs previous period.
_KPI_REVENUE = _kpi_hint("SUM(ct.thanh_tien)", _JOIN_LINE)
# KPI: distinct order count this vs previous period.
_KPI_ORDERS = _kpi_hint("COUNT(DISTINCT dh.don_hang_id)")
# KPI: distinct active customers this vs previous period.
_KPI_CUSTOMERS = _kpi_hint("COUNT(DISTINCT dh.khach_hang_id)")

# by_dimension: net revenue per group, this-period vs previous-period columns (so the
# profiler can compute per-row deltas + contributors, plan §15.1).
_BY_DIM_REVENUE = (
    "SELECT {label} AS nhom,\n"
    "       SUM(CASE WHEN dh.ngay_dat_hang BETWEEN '{{date_from}}' AND '{{date_to}}' "
    "THEN ct.thanh_tien ELSE 0 END) AS ky_nay,\n"
    "       SUM(CASE WHEN dh.ngay_dat_hang BETWEEN '{{compare_from}}' AND '{{compare_to}}' "
    "THEN ct.thanh_tien ELSE 0 END) AS ky_truoc\n"
    "FROM don_hang_ban dh\n"
    "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id{joins}\n"
    "WHERE dh.trang_thai = 'NORMAL' "
    "AND dh.ngay_dat_hang BETWEEN '{{compare_from}}' AND '{{date_to}}' {{entity_filter}}\n"
    "GROUP BY {label}\n"
    "ORDER BY ky_nay DESC"
)

_CAT_JOINS = (
    "\nJOIN san_pham sp ON ct.san_pham_id = sp.san_pham_id"
    "\nJOIN danh_muc_san_pham dm ON sp.danh_muc_id = dm.danh_muc_id"
)
_REGION_JOINS = (
    "\nJOIN nha_phan_phoi npp ON dh.nha_phan_phoi_id = npp.nha_phan_phoi_id"
    "\nJOIN vung v ON npp.vung_id = v.vung_id"
)

_BY_DIM_CATEGORY = _BY_DIM_REVENUE.format(label="dm.ten_danh_muc", joins=_CAT_JOINS)
_BY_DIM_REGION = _BY_DIM_REVENUE.format(label="v.ten_vung", joins=_REGION_JOINS)


# top_n: leaders by revenue this period (label + join injected).
def _top_n_hint(label: str, joins: str) -> str:
    return (
        "SELECT {label} AS ten, SUM(ct.thanh_tien) AS doanh_thu\n"
        "FROM don_hang_ban dh\n"
        "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id{joins}\n"
        "WHERE dh.trang_thai = 'NORMAL' "
        "AND dh.ngay_dat_hang BETWEEN '{{date_from}}' AND '{{date_to}}' {{entity_filter}}\n"
        "GROUP BY {label}\n"
        "ORDER BY doanh_thu DESC\n"
        "LIMIT 10"
    ).format(label=label, joins=joins)


_TOP_CUSTOMERS = _top_n_hint(
    "kh.ten_khach_hang",
    "\nJOIN khach_hang kh ON dh.khach_hang_id = kh.khach_hang_id")
_TOP_PRODUCTS = _top_n_hint(
    "sp.ten_san_pham",
    "\nJOIN san_pham sp ON ct.san_pham_id = sp.san_pham_id")
_TOP_DISTRIBUTORS = _top_n_hint(
    "npp.ten_nha_phan_phoi",
    "\nJOIN nha_phan_phoi npp ON dh.nha_phan_phoi_id = npp.nha_phan_phoi_id")

# trend: monthly net revenue over the window.
_TREND_MONTHLY = (
    "SELECT strftime('%Y-%m', dh.ngay_dat_hang) AS thang, SUM(ct.thanh_tien) AS doanh_thu\n"
    "FROM don_hang_ban dh\n"
    "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
    "WHERE dh.trang_thai = 'NORMAL' "
    "AND dh.ngay_dat_hang BETWEEN '{date_from}' AND '{date_to}' {entity_filter}\n"
    "GROUP BY strftime('%Y-%m', dh.ngay_dat_hang)\n"
    "ORDER BY thang"
)

# by_dimension: units (so_luong) per category this period.
_BY_DIM_UNITS_CATEGORY = (
    "SELECT dm.ten_danh_muc AS nhom, SUM(ct.so_luong) AS san_luong\n"
    "FROM don_hang_ban dh\n"
    "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
    "JOIN san_pham sp ON ct.san_pham_id = sp.san_pham_id\n"
    "JOIN danh_muc_san_pham dm ON sp.danh_muc_id = dm.danh_muc_id\n"
    "WHERE dh.trang_thai = 'NORMAL' "
    "AND dh.ngay_dat_hang BETWEEN '{date_from}' AND '{date_to}' {entity_filter}\n"
    "GROUP BY dm.ten_danh_muc\n"
    "ORDER BY san_luong DESC"
)

# channel (customer type) and province by_dimension, single period.
_BY_DIM_CHANNEL = (
    "SELECT lkh.ten_loai AS nhom, SUM(ct.thanh_tien) AS doanh_thu\n"
    "FROM don_hang_ban dh\n"
    "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
    "JOIN khach_hang kh ON dh.khach_hang_id = kh.khach_hang_id\n"
    "JOIN loai_khach_hang lkh ON kh.loai_khach_hang_id = lkh.loai_khach_hang_id\n"
    "WHERE dh.trang_thai = 'NORMAL' "
    "AND dh.ngay_dat_hang BETWEEN '{date_from}' AND '{date_to}' {entity_filter}\n"
    "GROUP BY lkh.ten_loai\n"
    "ORDER BY doanh_thu DESC"
)
_BY_DIM_PROVINCE = (
    "SELECT vt.tinh_thanh AS nhom, SUM(ct.thanh_tien) AS doanh_thu\n"
    "FROM don_hang_ban dh\n"
    "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
    "JOIN khach_hang kh ON dh.khach_hang_id = kh.khach_hang_id\n"
    "JOIN vi_tri vt ON kh.vi_tri_id = vt.vi_tri_id\n"
    "WHERE dh.trang_thai = 'NORMAL' "
    "AND dh.ngay_dat_hang BETWEEN '{date_from}' AND '{date_to}' {entity_filter}\n"
    "GROUP BY vt.tinh_thanh\n"
    "ORDER BY doanh_thu DESC"
)


# ---- Playbooks --------------------------------------------------------------
PLAYBOOKS: list[dict] = [
    {
        "playbook": "revenue_drop",
        "kind": "diagnostic",
        "aliases": [
            "vì sao doanh thu giảm", "tại sao doanh thu giảm", "doanh thu giảm",
            "phân tích doanh thu giảm", "nguyên nhân doanh thu giảm", "sụt giảm doanh thu",
            "sales dropped", "revenue drop", "why did revenue fall", "declining sales",
        ],
        "use_when": (
            "Người dùng hỏi vì sao doanh thu/doanh số giảm (hoặc tăng bất thường) trong một "
            "kỳ, cần tìm nguyên nhân gốc rễ: mất khách, giảm giá trị đơn, hay một ngành hàng "
            "kéo xuống. Diagnose revenue decline / root cause of a sales change."
        ),
        "main_metrics": ["doanh_thu", "so_don_hang", "so_khach_hang"],
        "required_comparison": "previous_period",
        "diagnostic_steps": [
            {"title": "Doanh thu kỳ này so với kỳ trước",
             "purpose": "Xác nhận và định lượng mức thay đổi doanh thu.",
             "metric": "doanh_thu", "expected_shape": "kpi", "sql_hint": _KPI_REVENUE},
            {"title": "Số đơn hàng kỳ này so với kỳ trước",
             "purpose": "Kiểm tra tần suất đặt hàng có giảm không.",
             "metric": "so_don_hang", "expected_shape": "kpi", "sql_hint": _KPI_ORDERS},
            {"title": "Số khách hàng hoạt động kỳ này so với kỳ trước",
             "purpose": "Kiểm tra có mất khách/độ phủ giảm không.",
             "metric": "so_khach_hang", "expected_shape": "kpi", "sql_hint": _KPI_CUSTOMERS},
            {"title": "Doanh thu theo ngành hàng, kỳ này so với kỳ trước",
             "purpose": "Tìm ngành hàng đóng góp lớn nhất vào mức giảm.",
             "metric": "doanh_thu", "dimension": "category",
             "expected_shape": "by_dimension", "sql_hint": _BY_DIM_CATEGORY},
            {"title": "Top khách hàng theo doanh thu kỳ này",
             "purpose": "Nhận diện khách hàng lớn để soi mức thay đổi.",
             "metric": "doanh_thu", "dimension": "customer",
             "expected_shape": "top_n", "sql_hint": _TOP_CUSTOMERS},
        ],
        "interpretation_rules": [
            "Nếu số khách hàng hoạt động giảm mạnh hơn giá trị đơn trung bình thì nguyên nhân "
            "chính có thể là mất khách hoặc độ phủ tuyến giảm.",
            "Nếu giá trị đơn trung bình (AOV = doanh thu / số đơn) giảm nhiều hơn thì có thể do "
            "giỏ hàng nhỏ hơn, đổi cơ cấu sản phẩm, hoặc tăng khuyến mãi.",
            "Nếu một ngành hàng chiếm phần lớn mức giảm (top-3 concentration > 50%) thì tập "
            "trung điều tra ngành hàng đó trước.",
        ],
        "improvement_rules": [
            "Khách hàng hoạt động giảm: lập danh sách khách đã mất, rà soát độ phủ tuyến và tần "
            "suất viếng thăm, ưu tiên tái kích hoạt.",
            "Giá trị đơn trung bình giảm: rà soát cơ cấu sản phẩm, đẩy bán combo/bundle và sản "
            "phẩm giá trị cao.",
            "Một ngành hàng giảm mạnh: kiểm tra tồn kho, giá bán và mức khuyến mãi của ngành đó.",
        ],
        "caveats": [
            "Chỉ tính đơn có trang_thai = 'NORMAL'.",
            "Không có dữ liệu tồn kho/khuyến mãi chi phí nên nguyên nhân chỉ mang tính tương quan.",
        ],
        "notes": "Ported from the plan's revenue-drop diagnostic (§9.4-9.5).",
    },
    {
        "playbook": "top_customer_analysis",
        "kind": "diagnostic",
        "aliases": [
            "phân tích khách hàng", "phân tích sâu khách hàng", "phân tích khách hàng top",
            "khách hàng này mua gì", "đánh giá khách hàng", "customer deep dive",
            "analyze customer", "top customer analysis",
        ],
        "use_when": (
            "Người dùng muốn phân tích sâu MỘT khách hàng/điểm bán cụ thể (thường là khách top "
            "từ kết quả trước): họ mua gì, xu hướng ra sao, kỳ này so kỳ trước thế nào. "
            "Deep-dive on a single customer entity."
        ),
        "main_metrics": ["doanh_thu", "so_don_hang"],
        "required_comparison": "previous_period",
        "diagnostic_steps": [
            {"title": "Doanh thu của khách hàng, kỳ này so với kỳ trước",
             "purpose": "Định lượng thay đổi doanh thu của khách hàng.",
             "metric": "doanh_thu", "expected_shape": "kpi", "sql_hint": _KPI_REVENUE},
            {"title": "Số đơn hàng của khách hàng, kỳ này so với kỳ trước",
             "purpose": "Kiểm tra tần suất mua hàng.",
             "metric": "so_don_hang", "expected_shape": "kpi", "sql_hint": _KPI_ORDERS},
            {"title": "Top sản phẩm khách hàng mua theo doanh thu",
             "purpose": "Hiểu cơ cấu giỏ hàng của khách.",
             "metric": "doanh_thu", "dimension": "product",
             "expected_shape": "top_n", "sql_hint": _TOP_PRODUCTS},
            {"title": "Xu hướng doanh thu theo tháng của khách hàng",
             "purpose": "Nhìn xu hướng mua theo thời gian.",
             "metric": "doanh_thu", "dimension": "month",
             "expected_shape": "trend", "sql_hint": _TREND_MONTHLY},
        ],
        "interpretation_rules": [
            "Nếu số đơn giảm nhưng doanh thu/đơn giữ nguyên thì khách mua thưa hơn (vấn đề độ phủ/viếng thăm).",
            "Nếu số đơn giữ nhưng doanh thu giảm thì khách thu hẹp giỏ hàng hoặc đổi sang sản phẩm rẻ hơn.",
        ],
        "improvement_rules": [
            "Số đơn giảm: tăng tần suất viếng thăm, kiểm tra tuyến phụ trách khách này.",
            "Giỏ hàng thu hẹp: giới thiệu thêm sản phẩm liên quan, ưu đãi combo cho khách này.",
        ],
        "caveats": ["Chỉ tính đơn có trang_thai = 'NORMAL'.",
                    "Cần lọc theo đúng mã khách hàng (entity filter)."],
        "notes": "Entity-scoped: {entity_filter} pins the customer id (Flow B).",
    },
    {
        "playbook": "product_category_performance",
        "kind": "comparison",
        "aliases": [
            "hiệu suất ngành hàng", "hiệu suất danh mục", "phân tích ngành hàng",
            "doanh thu theo ngành hàng", "so sánh danh mục", "category performance",
            "product category analysis", "phân tích sản phẩm",
        ],
        "use_when": (
            "Người dùng muốn đánh giá hiệu suất theo ngành hàng/danh mục sản phẩm: ngành nào "
            "tăng, ngành nào giảm, sản phẩm nào dẫn đầu. Category / product performance review."
        ),
        "main_metrics": ["doanh_thu", "so_luong_ban"],
        "required_comparison": "previous_period",
        "diagnostic_steps": [
            {"title": "Doanh thu theo ngành hàng, kỳ này so với kỳ trước",
             "purpose": "So sánh doanh thu từng ngành giữa hai kỳ.",
             "metric": "doanh_thu", "dimension": "category",
             "expected_shape": "by_dimension", "sql_hint": _BY_DIM_CATEGORY},
            {"title": "Sản lượng theo ngành hàng kỳ này",
             "purpose": "Xem khối lượng bán ra từng ngành.",
             "metric": "so_luong_ban", "dimension": "category",
             "expected_shape": "by_dimension", "sql_hint": _BY_DIM_UNITS_CATEGORY},
            {"title": "Top sản phẩm theo doanh thu kỳ này",
             "purpose": "Nhận diện sản phẩm dẫn đầu.",
             "metric": "doanh_thu", "dimension": "product",
             "expected_shape": "top_n", "sql_hint": _TOP_PRODUCTS},
            {"title": "Xu hướng doanh thu theo tháng",
             "purpose": "Nhìn xu hướng tổng theo thời gian.",
             "metric": "doanh_thu", "dimension": "month",
             "expected_shape": "trend", "sql_hint": _TREND_MONTHLY},
        ],
        "interpretation_rules": [
            "Ngành hàng có mức giảm tuyệt đối lớn nhất là ngành cần ưu tiên điều tra.",
            "Nếu doanh thu giảm nhưng sản lượng giữ thì nguyên nhân là giá/mix, không phải nhu cầu.",
        ],
        "improvement_rules": [
            "Ngành giảm mạnh: rà soát giá, khuyến mãi và độ phủ sản phẩm của ngành.",
            "Sản phẩm dẫn đầu: đảm bảo tồn kho và độ phủ để không mất doanh thu.",
        ],
        "caveats": ["Chỉ tính đơn có trang_thai = 'NORMAL'."],
        "notes": "",
    },
    {
        "playbook": "region_channel_comparison",
        "kind": "comparison",
        "aliases": [
            "so sánh vùng miền", "doanh thu theo vùng", "phân tích theo miền",
            "so sánh kênh bán", "hiệu suất theo khu vực", "region comparison",
            "channel comparison", "sales by region", "miền nào giảm",
        ],
        "use_when": (
            "Người dùng muốn so sánh hiệu suất theo vùng/miền, tỉnh thành, kênh bán (loại khách "
            "hàng) hoặc nhà phân phối: khu vực nào mạnh/yếu, miền nào giảm mạnh nhất. "
            "Region / channel / territory comparison."
        ),
        "main_metrics": ["doanh_thu"],
        "required_comparison": "previous_period",
        "diagnostic_steps": [
            {"title": "Doanh thu theo vùng, kỳ này so với kỳ trước",
             "purpose": "So sánh doanh thu từng vùng giữa hai kỳ.",
             "metric": "doanh_thu", "dimension": "region",
             "expected_shape": "by_dimension", "sql_hint": _BY_DIM_REGION},
            {"title": "Doanh thu theo kênh bán (loại khách hàng) kỳ này",
             "purpose": "So sánh hiệu suất theo kênh phân phối.",
             "metric": "doanh_thu", "dimension": "customer",
             "expected_shape": "by_dimension", "sql_hint": _BY_DIM_CHANNEL},
            {"title": "Doanh thu theo tỉnh thành kỳ này",
             "purpose": "Xem phân bố doanh thu theo địa lý.",
             "metric": "doanh_thu", "dimension": "city",
             "expected_shape": "by_dimension", "sql_hint": _BY_DIM_PROVINCE},
            {"title": "Top nhà phân phối theo doanh thu kỳ này",
             "purpose": "Nhận diện NPP dẫn đầu và tụt hạng.",
             "metric": "doanh_thu", "dimension": "distributor",
             "expected_shape": "top_n", "sql_hint": _TOP_DISTRIBUTORS},
        ],
        "interpretation_rules": [
            "Vùng có mức giảm tuyệt đối lớn nhất là nơi kéo tổng doanh thu xuống.",
            "Nếu một kênh (loại khách hàng) giảm mạnh thì vấn đề nằm ở kênh đó, không phải toàn thị trường.",
        ],
        "improvement_rules": [
            "Vùng yếu: rà soát độ phủ nhà phân phối và nhân sự bán hàng ở vùng đó.",
            "Kênh yếu: điều chỉnh chính sách giá/khuyến mãi phù hợp với kênh.",
        ],
        "caveats": ["Chỉ tính đơn có trang_thai = 'NORMAL'.",
                    "Vùng lấy theo nhà phân phối; tỉnh thành lấy theo vị trí khách hàng."],
        "notes": "",
    },
]


# ---- Dimensions -------------------------------------------------------------
DIMENSIONS: list[dict] = [
    {"dimension": "category", "table": "danh_muc_san_pham", "column": "ten_danh_muc",
     "id_column": "danh_muc_id", "join_requirement": "revenue_by_category",
     "drill_down_to": ["product"],
     "aliases": ["ngành hàng", "nganh hang", "danh mục", "danh muc", "nhóm sản phẩm", "category"],
     "use_when": "group or compare by product category / ngành hàng / danh mục"},
    {"dimension": "product", "table": "san_pham", "column": "ten_san_pham",
     "id_column": "san_pham_id", "join_requirement": "revenue_by_product",
     "drill_down_to": ["customer"],
     "aliases": ["sản phẩm", "san pham", "mặt hàng", "mat hang", "sku", "product"],
     "use_when": "group or compare by product / sản phẩm / SKU / mặt hàng"},
    {"dimension": "customer", "table": "khach_hang", "column": "ten_khach_hang",
     "id_column": "khach_hang_id", "join_requirement": "revenue_by_customer",
     "drill_down_to": ["product"],
     "aliases": ["khách hàng", "khach hang", "điểm bán", "diem ban", "outlet", "customer"],
     "use_when": "group or compare by customer / outlet / điểm bán / khách hàng"},
    {"dimension": "company", "table": "cong_ty", "column": "ten_cong_ty",
     "id_column": "cong_ty_id", "join_requirement": "revenue_by_company",
     "drill_down_to": ["category", "product"],
     "aliases": ["công ty", "cong ty", "nhà cung cấp", "nha cung cap", "thương hiệu", "company", "brand"],
     "use_when": "group or compare by company / brand owner / công ty / nhà cung cấp"},
    {"dimension": "distributor", "table": "nha_phan_phoi", "column": "ten_nha_phan_phoi",
     "id_column": "nha_phan_phoi_id", "join_requirement": "revenue_by_distributor",
     "drill_down_to": ["customer"],
     "aliases": ["nhà phân phối", "nha phan phoi", "npp", "distributor"],
     "use_when": "group or compare by distributor / NPP / nhà phân phối"},
    {"dimension": "region", "table": "vung", "column": "ten_vung",
     "id_column": "vung_id", "join_requirement": "revenue_by_region",
     "drill_down_to": ["city", "distributor"],
     "aliases": ["vùng", "vung", "miền", "mien", "khu vực", "khu vuc", "region", "territory"],
     "use_when": "group or compare by sales region / vùng / miền (Bắc, Trung, Nam)"},
    {"dimension": "city", "table": "vi_tri", "column": "tinh_thanh",
     "id_column": "vi_tri_id", "join_requirement": "revenue_by_province",
     "drill_down_to": ["customer"],
     "aliases": ["tỉnh thành", "tinh thanh", "thành phố", "thanh pho", "tỉnh", "province", "city"],
     "use_when": "group or compare by province / city / tỉnh thành (HCM, Hà Nội, Đà Nẵng)"},
    {"dimension": "month", "table": "don_hang_ban", "column": "ngay_dat_hang",
     "id_column": "", "join_requirement": "",
     "drill_down_to": ["category", "customer"],
     "aliases": ["tháng", "thang", "theo tháng", "theo thang", "hàng tháng", "monthly", "month", "over time"],
     "use_when": "trend over time / group by month using strftime('%Y-%m', ngay_dat_hang)"},
]


# ---- Chart rules (one per shape; NOT embedded, loaded fresh via kb_version) --
CHART_RULES: list[dict] = [
    {"shape": "kpi_comparison", "chart_type": "grouped_bar", "max_categories": 2, "min_rows": 2,
     "notes": "So sánh kỳ này với kỳ trước cho một chỉ số."},
    {"shape": "trend", "chart_type": "line", "max_categories": 36, "min_rows": 2,
     "notes": "Xu hướng theo thời gian (tháng)."},
    {"shape": "top_n", "chart_type": "horizontal_bar", "max_categories": 12, "min_rows": 2,
     "notes": "Xếp hạng top-N theo giá trị."},
    {"shape": "composition", "chart_type": "stacked_bar", "max_categories": 12, "min_rows": 2,
     "notes": "Cơ cấu đóng góp theo nhóm."},
    {"shape": "raw", "chart_type": "none", "max_categories": 0, "min_rows": 0,
     "notes": "Không vẽ biểu đồ; chỉ hiển thị bảng."},
]


# ---- Metric analytic extensions (merged onto existing metric entries, §10.2) --
METRIC_EXTENSIONS: dict[str, dict] = {
    "doanh_thu": {
        "direction": "higher_is_better",
        "decomposition": ["so_don_hang", "so_khach_hang", "gia_tri_don_trung_binh"],
        "default_comparisons": ["previous_period"],
        "default_dimensions": ["category", "customer", "region"],
        "interpretation_down": (
            "Doanh thu giảm thường do mất khách hàng (số khách hoạt động giảm), giảm tần suất "
            "đơn, giảm giá trị đơn trung bình, hoặc một ngành hàng/vùng kéo xuống."),
        "interpretation_up": (
            "Doanh thu tăng thường do thêm khách mới, tăng tần suất đơn, tăng giá trị đơn trung "
            "bình, hoặc một ngành hàng/vùng bứt phá."),
    },
    "so_luong_ban": {
        "direction": "higher_is_better",
        "default_comparisons": ["previous_period"],
        "default_dimensions": ["category", "product"],
        "interpretation_down": "Sản lượng giảm cho thấy nhu cầu/độ phủ giảm, độc lập với thay đổi giá.",
        "interpretation_up": "Sản lượng tăng cho thấy nhu cầu/độ phủ tăng.",
    },
    "so_don_hang": {
        "direction": "higher_is_better",
        "default_comparisons": ["previous_period"],
        "default_dimensions": ["customer", "region"],
        "interpretation_down": "Số đơn giảm cho thấy tần suất mua hoặc độ phủ tuyến giảm.",
        "interpretation_up": "Số đơn tăng cho thấy tần suất mua hoặc độ phủ tuyến tăng.",
    },
}


# ---- Caveats (parameterized by the data window, like business_meta.rules) -----
def caveats(data_min: str, data_max: str) -> list[dict]:
    return [
        {"title": "Phạm vi dữ liệu",
         "content": (f"Cơ sở dữ liệu chỉ có đơn hàng từ {data_min} đến {data_max}. Các câu hỏi "
                     f"về kỳ tương đối (\"tháng này\") phải neo vào MAX(ngay_dat_hang) hoặc một "
                     f"ngày cụ thể trong phạm vi này."),
         "applies_to_tables": ["don_hang_ban", "chi_tiet_don_hang_ban"],
         "severity": "warning",
         "aliases": ["phạm vi dữ liệu", "data window", "khoảng thời gian dữ liệu"]},
        {"title": "Chỉ tính đơn NORMAL",
         "content": ("Doanh thu và hầu hết chỉ số chỉ tính đơn có don_hang_ban.trang_thai = "
                     "'NORMAL'. Đơn CANCELLED bị loại khỏi doanh thu thực hiện."),
         "applies_to_metrics": ["doanh_thu", "so_don_hang"],
         "applies_to_tables": ["don_hang_ban"],
         "severity": "info",
         "aliases": ["đơn normal", "trạng thái đơn", "order status filter"]},
        {"title": "Doanh thu thuần",
         "content": ("doanh_thu = SUM(chi_tiet_don_hang_ban.thanh_tien) là doanh thu thuần (đã "
                     "trừ khuyến mãi). Doanh thu gộp = SUM(so_luong * don_gia); tổng đơn = "
                     "SUM(tong_tien) theo DISTINCT don_hang_id."),
         "applies_to_metrics": ["doanh_thu"],
         "applies_to_tables": ["chi_tiet_don_hang_ban"],
         "severity": "info",
         "aliases": ["doanh thu thuần", "net revenue", "định nghĩa doanh thu"]},
        {"title": "Thiếu dữ liệu tồn kho và chi phí",
         "content": ("Không có dữ liệu tồn kho, chi phí, hay nhật ký chi phí viếng thăm. Vì vậy "
                     "các nhận định về nguyên nhân chỉ mang tính tương quan, không phải nhân quả."),
         "severity": "warning",
         "aliases": ["thiếu dữ liệu", "no inventory data", "no cost data"]},
        {"title": "Giới hạn mẫu giá trị",
         "content": ("Danh sách giá trị (tên khách hàng, sản phẩm...) được lấy mẫu giới hạn; một "
                     "thực thể không nằm trong mẫu vẫn có thể xuất hiện trong dữ liệu bán."),
         "severity": "info",
         "aliases": ["giới hạn mẫu", "value sampling", "entity sampling"]},
        {"title": "Kỳ hiện tại chưa đủ dữ liệu",
         "content": (f"Kỳ/tháng gần nhất có thể chưa đủ dữ liệu vì dữ liệu chỉ đến {data_max}. So "
                     f"sánh kỳ hiện tại chưa hoàn thành với kỳ trước có thể gây hiểu nhầm là giảm."),
         "applies_to_metrics": ["doanh_thu"],
         "severity": "warning",
         "aliases": ["kỳ chưa đủ", "incomplete period", "partial period"]},
    ]


def build_analysis_entries(data_min: str, data_max: str) -> list[dict]:
    """Flat list of {type, body} analytic entries to stage (playbooks, dimensions,
    caveats, chart_rules). Metric extensions are applied to metric entries separately
    in ``seed.build_entries``."""
    entries: list[dict] = []
    for pb in PLAYBOOKS:
        entries.append({"type": "playbook", "body": dict(pb)})
    for dm in DIMENSIONS:
        entries.append({"type": "dimension", "body": dict(dm)})
    for cv in caveats(data_min, data_max):
        entries.append({"type": "caveat", "body": dict(cv)})
    for cr in CHART_RULES:
        entries.append({"type": "chart_rule", "body": dict(cr)})
    return entries
