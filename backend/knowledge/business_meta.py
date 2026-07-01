"""Curated business knowledge for the FMCG sales DB (the hand-authored layer).

Structural facts (tables/columns/PK/FK) come from `common/schema_def.py`; real
values come from `sales.db`. This module adds what only a human knows: metric
formulas, named join paths, which columns hold user-nameable values, enum meanings,
global SQL rules, and English/use-when enrichment per table & key column.

All SQL is SQLite. Canonical revenue `doanh_thu` = SUM(chi_tiet_don_hang_ban.thanh_tien)
(net, after promotions). Gross and order-header totals are documented alternatives.
"""
from __future__ import annotations

# ---- Metrics ----------------------------------------------------------------
METRICS: list[dict] = [
    {
        "metric": "doanh_thu",
        "aliases": ["doanh thu", "doanh số", "doanh so", "sales", "revenue",
                    "tổng tiền bán hàng", "tong tien ban hang", "net sales", "doanh thu thuần"],
        "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "required_tables": ["don_hang_ban", "chi_tiet_don_hang_ban"],
        "required_joins": ["don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "user asks about revenue / doanh thu / doanh so / sales amount / total money from orders (net, after promotions)",
        "notes": ("Canonical net revenue using thanh_tien (already discounted). Count only realized "
                  "orders with don_hang_ban.trang_thai = 'NORMAL'. Gross (pre-discount) = "
                  "SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia). Order-header "
                  "total = SUM(don_hang_ban.tong_tien) over DISTINCT don_hang_id (do not sum tong_tien "
                  "after joining line items -- it double counts)."),
    },
    {
        "metric": "doanh_thu_gross",
        "aliases": ["doanh thu gộp", "doanh thu goc", "gross revenue", "revenue before discount",
                    "doanh thu trước giảm giá", "gia tri niem yet"],
        "formula": "SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia)",
        "required_tables": ["don_hang_ban", "chi_tiet_don_hang_ban"],
        "required_joins": ["don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "user explicitly asks revenue before discount / list-price value (not the default revenue)",
        "notes": "Pre-discount gross. Default revenue questions should use doanh_thu (net) instead.",
    },
    {
        "metric": "so_luong_ban",
        "aliases": ["số lượng bán", "so luong ban", "units sold", "quantity sold", "sản lượng", "san luong"],
        "formula": "SUM(chi_tiet_don_hang_ban.so_luong)",
        "required_tables": ["chi_tiet_don_hang_ban"],
        "required_joins": [],
        "use_when": "user asks about quantity / units / số lượng bán / sản lượng of products",
        "notes": "Join to don_hang_ban only when filtering by order attributes (date, customer, status).",
    },
    {
        "metric": "so_don_hang",
        "aliases": ["số đơn hàng", "so don hang", "number of orders", "order count", "số lượng đơn"],
        "formula": "COUNT(DISTINCT don_hang_ban.don_hang_id)",
        "required_tables": ["don_hang_ban"],
        "required_joins": [],
        "use_when": "user asks how many orders / order count / order frequency",
        "notes": "Use DISTINCT don_hang_id when the query also joins chi_tiet_don_hang_ban.",
    },
    {
        "metric": "so_khach_hang",
        "aliases": ["số khách hàng", "so khach hang", "number of customers", "distinct customers", "khách mua"],
        "formula": "COUNT(DISTINCT don_hang_ban.khach_hang_id)",
        "required_tables": ["don_hang_ban"],
        "required_joins": [],
        "use_when": "user asks how many distinct customers ordered / number of buying outlets",
        "notes": "For all registered customers (not just buyers) use COUNT(*) on khach_hang.",
    },
    {
        "metric": "so_luong_tra",
        "aliases": ["số lượng trả về", "so luong tra", "returned units", "hàng trả về", "return quantity"],
        "formula": "SUM(hang_tra_ve.so_luong)",
        "required_tables": ["hang_tra_ve"],
        "required_joins": [],
        "use_when": "user asks about returned quantity / returns / hàng trả về",
        "notes": ("Return rate = returned units / sold units. Compute the two sums in separate "
                  "subqueries (one over hang_tra_ve, one over chi_tiet_don_hang_ban); do not join the "
                  "two fact tables directly or the counts multiply."),
    },
    {
        "metric": "ty_le_vieng_tham_thanh_cong",
        "aliases": ["tỷ lệ viếng thăm thành công", "ty le vieng tham thanh cong",
                    "visit success rate", "tỷ lệ chốt đơn", "ordered visit rate", "conversion rate"],
        "formula": "SUM(CASE WHEN lich_su_vieng_tham.ket_qua = 'ORDERED' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)",
        "required_tables": ["lich_su_vieng_tham"],
        "required_joins": [],
        "use_when": "user asks about visit success / conversion of visits into orders / ordered rate",
        "notes": "ket_qua = 'ORDERED' marks a visit that produced an order.",
    },
]

# ---- Named join paths -------------------------------------------------------
JOIN_PATHS: list[dict] = [
    {
        "name": "revenue_by_company",
        "tables": ["cong_ty", "don_hang_ban", "chi_tiet_don_hang_ban"],
        "joins": ["cong_ty.cong_ty_id = don_hang_ban.cong_ty_id",
                  "don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo công ty / sales by company / revenue by brand owner",
    },
    {
        "name": "revenue_by_customer",
        "tables": ["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
        "joins": ["khach_hang.khach_hang_id = don_hang_ban.khach_hang_id",
                  "don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo khách hàng / top customers by revenue",
    },
    {
        "name": "revenue_by_product",
        "tables": ["san_pham", "chi_tiet_don_hang_ban", "don_hang_ban"],
        "joins": ["san_pham.san_pham_id = chi_tiet_don_hang_ban.san_pham_id",
                  "chi_tiet_don_hang_ban.don_hang_id = don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo sản phẩm / best selling products / product revenue / units sold by product",
    },
    {
        "name": "revenue_by_category",
        "tables": ["danh_muc_san_pham", "san_pham", "chi_tiet_don_hang_ban", "don_hang_ban"],
        "joins": ["danh_muc_san_pham.danh_muc_id = san_pham.danh_muc_id",
                  "san_pham.san_pham_id = chi_tiet_don_hang_ban.san_pham_id",
                  "chi_tiet_don_hang_ban.don_hang_id = don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo danh mục / sales by category (đồ uống, bánh kẹo, sữa, ...)",
    },
    {
        "name": "revenue_by_province",
        "tables": ["vi_tri", "khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
        "joins": ["vi_tri.vi_tri_id = khach_hang.vi_tri_id",
                  "khach_hang.khach_hang_id = don_hang_ban.khach_hang_id",
                  "don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo tỉnh thành / sales by province or city (e.g. HCM, Ha Noi, Da Nang)",
    },
    {
        "name": "revenue_by_distributor",
        "tables": ["nha_phan_phoi", "don_hang_ban", "chi_tiet_don_hang_ban"],
        "joins": ["nha_phan_phoi.nha_phan_phoi_id = don_hang_ban.nha_phan_phoi_id",
                  "don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo nhà phân phối / sales by distributor / NPP performance",
    },
    {
        "name": "revenue_by_region",
        "tables": ["vung", "nha_phan_phoi", "don_hang_ban", "chi_tiet_don_hang_ban"],
        "joins": ["vung.vung_id = nha_phan_phoi.vung_id",
                  "nha_phan_phoi.nha_phan_phoi_id = don_hang_ban.nha_phan_phoi_id",
                  "don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo vùng / sales by region (miền Bắc, miền Trung, miền Nam, ...)",
    },
    {
        "name": "sales_by_staff",
        "tables": ["nhan_vien", "don_hang_ban", "chi_tiet_don_hang_ban"],
        "joins": ["nhan_vien.nhan_vien_id = don_hang_ban.nhan_vien_id",
                  "don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        "use_when": "doanh thu theo nhân viên / sales performance by salesperson",
    },
    {
        "name": "visits_by_customer",
        "tables": ["lich_su_vieng_tham", "khach_hang"],
        "joins": ["khach_hang.khach_hang_id = lich_su_vieng_tham.khach_hang_id"],
        "use_when": "viếng thăm theo khách hàng / visit history and results per outlet",
    },
    {
        "name": "returns_by_product",
        "tables": ["hang_tra_ve", "san_pham"],
        "joins": ["san_pham.san_pham_id = hang_tra_ve.san_pham_id"],
        "use_when": "hàng trả về theo sản phẩm / most returned products",
    },
]

# ---- Value-source columns (distinct values sampled from the DB) --------------
VALUE_SOURCES: list[dict] = [
    {"table": "cong_ty", "column": "ten_cong_ty", "id_column": "cong_ty_id"},
    {"table": "vung", "column": "ten_vung", "id_column": "vung_id"},
    {"table": "nha_phan_phoi", "column": "ten_nha_phan_phoi", "id_column": "nha_phan_phoi_id"},
    {"table": "nhan_vien", "column": "ten_nhan_vien", "id_column": "nhan_vien_id"},
    {"table": "khach_hang", "column": "ten_khach_hang", "id_column": "khach_hang_id"},
    {"table": "san_pham", "column": "ten_san_pham", "id_column": "san_pham_id"},
    {"table": "danh_muc_san_pham", "column": "ten_danh_muc", "id_column": "danh_muc_id"},
    {"table": "loai_khach_hang", "column": "ten_loai", "id_column": "loai_khach_hang_id"},
    {"table": "vi_tri", "column": "tinh_thanh"},
    {"table": "khuyen_mai", "column": "ten_khuyen_mai", "id_column": "khuyen_mai_id"},
]

# ---- Enum values (curated aliases; the value IS the stored code) --------------
ENUM_VALUES: list[dict] = [
    {"table": "don_hang_ban", "column": "trang_thai", "value": "NORMAL",
     "aliases": ["đơn bình thường", "đơn hợp lệ", "don binh thuong"],
     "use_when": "realized/valid orders; revenue should usually filter trang_thai = 'NORMAL'"},
    {"table": "don_hang_ban", "column": "trang_thai", "value": "CANCELLED",
     "aliases": ["đơn bị hủy", "hủy đơn", "don huy", "cancelled order"],
     "use_when": "user asks about cancelled orders"},
    {"table": "lich_su_vieng_tham", "column": "ket_qua", "value": "ORDERED",
     "aliases": ["có đơn", "chốt đơn", "co don", "phát sinh đơn"],
     "use_when": "visits that produced an order"},
    {"table": "lich_su_vieng_tham", "column": "ket_qua", "value": "NO_ORDER",
     "aliases": ["không đơn", "không phát sinh đơn", "khong don"],
     "use_when": "visits with no order"},
    {"table": "lich_su_vieng_tham", "column": "ket_qua", "value": "STORE_CLOSED",
     "aliases": ["đóng cửa", "cửa hàng đóng", "dong cua"], "use_when": "outlet was closed at visit"},
    {"table": "lich_su_vieng_tham", "column": "ket_qua", "value": "CUSTOMER_BUSY",
     "aliases": ["khách bận", "ban"], "use_when": "customer was busy at visit"},
    {"table": "lich_su_vieng_tham", "column": "ket_qua", "value": "NOT_FOUND",
     "aliases": ["không tìm thấy", "khong tim thay"], "use_when": "outlet not found"},
    {"table": "don_giao_hang", "column": "trang_thai", "value": "DELIVERED",
     "aliases": ["giao thành công", "đã giao", "da giao"], "use_when": "successfully delivered orders"},
    {"table": "don_giao_hang", "column": "trang_thai", "value": "SHIPPED",
     "aliases": ["đã xuất kho", "đang giao", "da xuat kho"], "use_when": "shipped but not yet delivered"},
    {"table": "don_giao_hang", "column": "trang_thai", "value": "FAILED",
     "aliases": ["giao thất bại", "giao hỏng", "that bai"], "use_when": "failed deliveries"},
]

# ---- Global rules (rendered into skill.md; NOT embedded) --------------------
def rules(data_min: str, data_max: str) -> list[dict]:
    return [
        {"section": "dialect", "title": "SQL Dialect",
         "content": "SQLite. Use SQLite functions (date(), strftime('%Y-%m', col)). "
                    "Do NOT use MySQL functions like DATE_FORMAT/CURDATE/DATE_ADD."},
        {"section": "global", "title": "Global SQL Rules", "items": [
            "Use only the provided tables, columns, and joins.",
            "Do not invent tables, columns, or join conditions.",
            "Use exact table and column names.",
            "Return one executable SQLite SELECT query when SQL is needed.",
            "SELECT only: never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or CREATE.",
            "Database identifiers use Vietnamese không dấu with snake_case; user questions may be có dấu.",
            "For revenue / doanh thu / doanh so / sales use the provided doanh_thu formula (net, thanh_tien).",
            "Add a LIMIT for exploratory / top-N questions.",
        ]},
        {"section": "normalization", "title": "Vietnamese Normalization Rules", "items": [
            "công ty -> cong_ty", "khách hàng -> khach_hang", "nhà phân phối -> nha_phan_phoi",
            "nhân viên -> nhan_vien", "đơn hàng bán -> don_hang_ban",
            "chi tiết đơn hàng bán -> chi_tiet_don_hang_ban", "sản phẩm -> san_pham",
            "danh mục -> danh_muc_san_pham", "khuyến mãi -> khuyen_mai", "viếng thăm -> lich_su_vieng_tham",
            "tỉnh thành / thành phố -> vi_tri.tinh_thanh", "vùng / miền -> vung.ten_vung",
            "doanh thu / doanh số -> doanh_thu", "ngày đặt hàng -> don_hang_ban.ngay_dat_hang",
        ]},
        {"section": "data_window", "title": "Data Coverage",
         "content": f"The database only holds orders from {data_min} to {data_max}. "
                    f"date('now') is outside this window, so relative periods like \"this month\" "
                    f"return nothing -- anchor recent-period questions to MAX(ngay_dat_hang) or an "
                    f"explicit date in range."},
        {"section": "metric_policy", "title": "Revenue & Metric Policy",
         "content": "Canonical doanh_thu = SUM(chi_tiet_don_hang_ban.thanh_tien) (net, after promotions), "
                    "over NORMAL orders. Gross (pre-discount) = SUM(so_luong * don_gia). Order-header "
                    "total = SUM(don_hang_ban.tong_tien) over DISTINCT don_hang_id. City/province filters "
                    "(e.g. Ho Chi Minh) go through khach_hang.vi_tri_id -> vi_tri.tinh_thanh."},
    ]

# ---- Per-table enrichment (English meaning + when to use) --------------------
# schema_def already provides the Vietnamese description, aliases, columns, and FKs.
TABLE_ENRICH: dict[str, dict] = {
    "cong_ty": {"meaning_en": "FMCG company / brand owner / supplier.",
                "use_when": ["company", "brand owner", "supplier", "công ty", "doanh nghiệp", "sản phẩm/doanh thu theo công ty"],
                "dont_use_when": ["revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban"]},
    "vung": {"meaning_en": "Sales region/territory in Vietnam (North, Central, South, ...).",
             "use_when": ["region", "territory", "miền", "vùng", "doanh thu theo vùng"],
             "dont_use_when": ["revenue alone -- reach orders via nha_phan_phoi"]},
    "nha_phan_phoi": {"meaning_en": "Distributor serving customers, routes, staff and orders in a region.",
                      "use_when": ["distributor", "NPP", "nhà phân phối", "doanh thu theo nhà phân phối"],
                      "dont_use_when": ["revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban"]},
    "vi_tri": {"meaning_en": "Geographic location: province/city, district, ward, and coordinates.",
               "use_when": ["province", "city", "tỉnh thành", "quận huyện", "HCM / Ha Noi / Da Nang", "doanh thu theo tỉnh"],
               "dont_use_when": ["revenue alone -- join via khach_hang then don_hang_ban"]},
    "tuyen_ban_hang": {"meaning_en": "Sales route owned by a distributor, tied to a region and location.",
                       "use_when": ["route", "tuyến bán hàng", "visit route", "delivery route"]},
    "nhan_vien": {"meaning_en": "Sales staff who visit customers and create orders.",
                  "use_when": ["staff", "salesperson", "nhân viên", "sales performance", "doanh thu theo nhân viên"],
                  "dont_use_when": ["revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban"]},
    "phan_cong_tuyen": {"meaning_en": "Assignment of staff to a route for a time period.",
                        "use_when": ["route assignment", "phân công tuyến", "who covers which route"]},
    "loai_khach_hang": {"meaning_en": "Customer/outlet type (grocery, mini-supermarket, wholesale, ...).",
                        "use_when": ["customer type", "channel", "loại khách hàng", "kênh bán"]},
    "khach_hang": {"meaning_en": "Retail customer / outlet visited by staff and served by a distributor.",
                   "use_when": ["customer", "outlet", "shop", "khách hàng", "điểm bán", "top khách hàng"],
                   "dont_use_when": ["revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban"]},
    "nha_phan_phoi_khach_hang": {"meaning_en": "Distributor-customer relationship with current staff and route.",
                                 "use_when": ["which distributor serves a customer", "current route of a customer", "customer mapping"]},
    "danh_muc_san_pham": {"meaning_en": "Product category (beverages, snacks, dairy, home, care, frozen).",
                          "use_when": ["category", "danh mục", "doanh thu theo danh mục"],
                          "dont_use_when": ["revenue alone -- join via san_pham then chi_tiet_don_hang_ban"]},
    "san_pham": {"meaning_en": "Product / SKU owned by a company and grouped by category.",
                 "use_when": ["product", "SKU", "sản phẩm", "mặt hàng", "best selling products"],
                 "dont_use_when": ["revenue alone -- join to chi_tiet_don_hang_ban"]},
    "bang_gia_san_pham": {"meaning_en": "Product price list effective over a date range.",
                          "use_when": ["price", "unit price", "bảng giá", "giá bán tại thời điểm"]},
    "khuyen_mai": {"meaning_en": "Promotion campaign with a date range and discount percent.",
                   "use_when": ["promotion", "discount", "khuyến mãi", "chương trình giảm giá"]},
    "khuyen_mai_san_pham": {"meaning_en": "Many-to-many between promotions and products.",
                            "use_when": ["which products are in a promotion", "sản phẩm khuyến mãi"]},
    "lich_su_vieng_tham": {"meaning_en": "Customer visit history by staff, with result and notes.",
                           "use_when": ["visit", "viếng thăm", "visit result", "ordered/no-order visits", "tỷ lệ chốt đơn"]},
    "don_hang_ban": {"meaning_en": "Sales order header created by staff for a customer, usually from a visit.",
                     "use_when": ["order", "đơn hàng", "revenue", "doanh thu", "order date", "order count"]},
    "chi_tiet_don_hang_ban": {"meaning_en": "Sales order line: product, quantity, unit price, promotion, line total (net).",
                              "use_when": ["order line", "revenue", "doanh thu", "units sold", "product revenue", "thành tiền"]},
    "don_giao_hang": {"meaning_en": "Delivery record for an order, with warehouse-release and delivery dates.",
                      "use_when": ["delivery", "shipment", "giao hàng", "delivered/failed deliveries"]},
    "hang_tra_ve": {"meaning_en": "Returned goods tied to the original order and product.",
                    "use_when": ["return", "refund", "hàng trả về", "returned quantity", "damaged goods"]},
}

# ---- Per-column enrichment (English meaning + aliases + when to use) ---------
# Only the columns users commonly reference; others fall back to schema_def desc.
COLUMN_ENRICH: dict[str, dict] = {
    "don_hang_ban.ngay_dat_hang": {
        "meaning": "Order date; used for time filtering and order-frequency/trend analysis.",
        "aliases": ["ngày đặt hàng", "ngày bán", "ngày phát sinh đơn", "order date", "thời gian bán"],
        "use_when": ["today", "this month", "this year", "date range", "sales period", "theo tháng/quý/năm"]},
    "don_hang_ban.trang_thai": {
        "meaning": "Order status: NORMAL or CANCELLED.",
        "aliases": ["trạng thái đơn", "đơn hủy", "order status"],
        "use_when": ["valid vs cancelled orders", "realized revenue filters NORMAL"]},
    "don_hang_ban.tong_tien": {
        "meaning": "Order-header total amount (VND).",
        "aliases": ["tổng tiền", "order total", "giá trị đơn"],
        "use_when": ["order-level total; sum over DISTINCT don_hang_id to avoid double counting"]},
    "chi_tiet_don_hang_ban.thanh_tien": {
        "meaning": "Line total after discount (VND) -- the basis of net revenue doanh_thu.",
        "aliases": ["thành tiền", "line total", "net line amount", "tiền dòng hàng"],
        "use_when": ["net revenue", "doanh thu", "sales value"]},
    "chi_tiet_don_hang_ban.so_luong": {
        "meaning": "Quantity ordered on the line.",
        "aliases": ["số lượng", "quantity", "units", "sản lượng"],
        "use_when": ["units sold", "quantity sold", "số lượng bán"]},
    "chi_tiet_don_hang_ban.don_gia": {
        "meaning": "Unit selling price on the line (VND).",
        "aliases": ["đơn giá", "unit price", "giá bán"],
        "use_when": ["price per unit", "gross revenue = so_luong * don_gia"]},
    "lich_su_vieng_tham.ket_qua": {
        "meaning": "Visit result: VISITED, ORDERED, NO_ORDER, STORE_CLOSED, CUSTOMER_BUSY, NOT_FOUND.",
        "aliases": ["kết quả viếng thăm", "visit result", "chốt đơn", "không đơn"],
        "use_when": ["visit success rate", "ordered vs no-order visits"]},
    "lich_su_vieng_tham.ngay_vieng_tham": {
        "meaning": "Visit date.",
        "aliases": ["ngày viếng thăm", "visit date"],
        "use_when": ["visits over time", "visits in a period"]},
    "vi_tri.tinh_thanh": {
        "meaning": "Province or city name (e.g. Ho Chi Minh, Ha Noi, Da Nang).",
        "aliases": ["tỉnh thành", "thành phố", "tỉnh", "province", "city", "HCM", "TPHCM"],
        "use_when": ["filter or group by city/province", "sales by location"]},
    "vung.ten_vung": {
        "meaning": "Region name (Mien Bac, Mien Trung, Mien Nam, Tay Nguyen, Mekong).",
        "aliases": ["tên vùng", "miền", "khu vực", "region name"],
        "use_when": ["group revenue by region"]},
    "san_pham.ten_san_pham": {
        "meaning": "Product name.",
        "aliases": ["tên sản phẩm", "product name", "mặt hàng"],
        "use_when": ["identify a product by name"]},
    "khach_hang.ten_khach_hang": {
        "meaning": "Customer / outlet name.",
        "aliases": ["tên khách hàng", "customer name", "tên điểm bán"],
        "use_when": ["identify a customer by name"]},
    "san_pham.trang_thai": {
        "meaning": "Product status: ACTIVE or INACTIVE.",
        "aliases": ["trạng thái sản phẩm", "còn bán", "ngừng bán"],
        "use_when": ["active vs discontinued products"]},
    "nha_phan_phoi.trang_thai": {
        "meaning": "Distributor status: ACTIVE or INACTIVE.",
        "aliases": ["trạng thái nhà phân phối"],
        "use_when": ["active distributors"]},
}
