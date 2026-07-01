"""Vietnamese-style 20-table FMCG database schema.

Copied verbatim from the old pipeline (schema_rag/schema_def.py). Single source of
truth for table/column structure, primary keys, and foreign-key edges. The live
`sales.db` is introduced/validated against this.

Table names intentionally use Vietnamese domain words without accents, e.g.
`khach_hang`, `vi_tri`, `nha_phan_phoi`, so generated SQL remains portable.
"""
from __future__ import annotations

from typing import Dict, List


TABLES: List[dict] = [
    {
        "name": "cong_ty",
        "description": "Công ty FMCG hoặc chủ thương hiệu bán sản phẩm thông qua nhà phân phối.",
        "aliases": ["company", "brand owner", "cong ty", "doanh nghiep"],
        "columns": [
            {"name": "cong_ty_id", "type": "TEXT", "pk": True, "desc": "Mã công ty."},
            {"name": "ten_cong_ty", "type": "TEXT", "desc": "Tên công ty."},
            {"name": "nganh_hang", "type": "TEXT", "desc": "Ngành hàng kinh doanh như FMCG hoặc đồ uống."},
        ],
        "foreign_keys": [],
    },
    {
        "name": "vung",
        "description": "Vùng bán hàng tại Việt Nam như miền Bắc, miền Trung, miền Nam.",
        "aliases": ["region", "territory", "mien", "khu vuc", "vung ban hang"],
        "columns": [
            {"name": "vung_id", "type": "TEXT", "pk": True, "desc": "Mã vùng."},
            {"name": "ten_vung", "type": "TEXT", "desc": "Tên vùng bán hàng."},
            {"name": "quoc_gia", "type": "TEXT", "desc": "Tên quốc gia."},
        ],
        "foreign_keys": [],
    },
    {
        "name": "nha_phan_phoi",
        "description": "Nhà phân phối phục vụ khách hàng, tuyến, nhân viên, viếng thăm và đơn hàng trong một vùng.",
        "aliases": [
            "distributor",
            "nha phan phoi",
            "NPP",
            "wholesale partner",
            "sales by distributor",
            "customers by distributor",
        ],
        "columns": [
            {"name": "nha_phan_phoi_id", "type": "TEXT", "pk": True, "desc": "Mã nhà phân phối."},
            {"name": "cong_ty_id", "type": "TEXT", "desc": "Mã công ty mà nhà phân phối trực thuộc."},
            {"name": "vung_id", "type": "TEXT", "desc": "Mã vùng hoạt động của nhà phân phối."},
            {"name": "ten_nha_phan_phoi", "type": "TEXT", "desc": "Tên nhà phân phối."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái ACTIVE hoặc INACTIVE."},
        ],
        "foreign_keys": [
            {"column": "cong_ty_id", "ref_table": "cong_ty", "ref_column": "cong_ty_id"},
            {"column": "vung_id", "ref_table": "vung", "ref_column": "vung_id"},
        ],
    },
    {
        "name": "vi_tri",
        "description": "Vị trí địa lý ở cấp tỉnh thành, quận huyện, phường xã và tọa độ.",
        "aliases": ["location", "dia diem", "vi tri", "province", "district", "ward", "toa do"],
        "columns": [
            {"name": "vi_tri_id", "type": "TEXT", "pk": True, "desc": "Mã vị trí."},
            {"name": "tinh_thanh", "type": "TEXT", "desc": "Tỉnh hoặc thành phố."},
            {"name": "quan_huyen", "type": "TEXT", "desc": "Quận hoặc huyện."},
            {"name": "phuong_xa", "type": "TEXT", "desc": "Phường hoặc xã."},
            {"name": "vi_do", "type": "REAL", "desc": "Vĩ độ."},
            {"name": "kinh_do", "type": "REAL", "desc": "Kinh độ."},
        ],
        "foreign_keys": [],
    },
    {
        "name": "tuyen_ban_hang",
        "description": "Tuyến bán hàng thuộc một nhà phân phối và gắn với vùng cùng vị trí.",
        "aliases": ["route", "sales route", "tuyen", "routing", "visit route", "delivery route"],
        "columns": [
            {"name": "tuyen_id", "type": "INTEGER", "pk": True, "desc": "Mã tuyến bán hàng."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối sở hữu tuyến."},
            {"name": "vung_id", "type": "TEXT", "desc": "Mã vùng của tuyến."},
            {"name": "vi_tri_id", "type": "TEXT", "desc": "Mã vị trí chính của tuyến."},
            {"name": "ma_tuyen", "type": "TEXT", "desc": "Mã định danh tuyến."},
            {"name": "ten_tuyen", "type": "TEXT", "desc": "Tên tuyến bán hàng."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái ACTIVE hoặc INACTIVE."},
        ],
        "foreign_keys": [
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"},
            {"column": "vung_id", "ref_table": "vung", "ref_column": "vung_id"},
            {"column": "vi_tri_id", "ref_table": "vi_tri", "ref_column": "vi_tri_id"},
        ],
    },
    {
        "name": "nhan_vien",
        "description": "Nhân viên bán hàng viếng thăm khách hàng và tạo đơn hàng cho nhà phân phối.",
        "aliases": ["staff", "salesperson", "sales rep", "nhan vien", "trinh duoc vien", "sales performance"],
        "columns": [
            {"name": "nhan_vien_id", "type": "TEXT", "pk": True, "desc": "Mã nhân viên."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối tuyển dụng nhân viên."},
            {"name": "ten_nhan_vien", "type": "TEXT", "desc": "Tên nhân viên."},
            {"name": "ngay_vao_lam", "type": "TEXT", "desc": "Ngày vào làm."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái ACTIVE hoặc INACTIVE."},
        ],
        "foreign_keys": [
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"}
        ],
    },
    {
        "name": "phan_cong_tuyen",
        "description": "Phân công nhân viên phụ trách tuyến bán hàng trong một khoảng thời gian.",
        "aliases": ["route assignment", "phan cong tuyen", "staff route", "assigned route"],
        "columns": [
            {"name": "phan_cong_id", "type": "INTEGER", "pk": True, "desc": "Mã phân công."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối."},
            {"name": "nhan_vien_id", "type": "TEXT", "desc": "Mã nhân viên được phân công."},
            {"name": "tuyen_id", "type": "INTEGER", "desc": "Mã tuyến được phân công."},
            {"name": "ngay_bat_dau", "type": "TEXT", "desc": "Ngày bắt đầu phân công."},
            {"name": "ngay_ket_thuc", "type": "TEXT", "desc": "Ngày kết thúc, có thể để trống."},
        ],
        "foreign_keys": [
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"},
            {"column": "nhan_vien_id", "ref_table": "nhan_vien", "ref_column": "nhan_vien_id"},
            {"column": "tuyen_id", "ref_table": "tuyen_ban_hang", "ref_column": "tuyen_id"},
        ],
    },
    {
        "name": "loai_khach_hang",
        "description": "Loại điểm bán hoặc khách hàng như tạp hóa, siêu thị mini, đại lý sỉ.",
        "aliases": ["customer type", "loai khach hang", "channel", "outlet type"],
        "columns": [
            {"name": "loai_khach_hang_id", "type": "TEXT", "pk": True, "desc": "Mã loại khách hàng."},
            {"name": "ten_loai", "type": "TEXT", "desc": "Tên loại khách hàng."},
            {"name": "mo_ta", "type": "TEXT", "desc": "Mô tả loại khách hàng."},
        ],
        "foreign_keys": [],
    },
    {
        "name": "khach_hang",
        "description": "Khách hàng bán lẻ hoặc điểm bán được nhân viên viếng thăm và nhà phân phối phục vụ.",
        "aliases": [
            "customer",
            "khach hang",
            "outlet",
            "shop",
            "retail store",
            "visit customer",
            "customer order frequency",
        ],
        "columns": [
            {"name": "khach_hang_id", "type": "TEXT", "pk": True, "desc": "Mã khách hàng."},
            {"name": "loai_khach_hang_id", "type": "TEXT", "desc": "Mã loại khách hàng."},
            {"name": "vi_tri_id", "type": "TEXT", "desc": "Mã vị trí của khách hàng."},
            {"name": "ten_khach_hang", "type": "TEXT", "desc": "Tên khách hàng hoặc điểm bán."},
            {"name": "dia_chi", "type": "TEXT", "desc": "Địa chỉ."},
            {"name": "so_dien_thoai", "type": "TEXT", "desc": "Số điện thoại."},
            {"name": "ngay_tao", "type": "TEXT", "desc": "Ngày tạo khách hàng."},
        ],
        "foreign_keys": [
            {"column": "loai_khach_hang_id", "ref_table": "loai_khach_hang", "ref_column": "loai_khach_hang_id"},
            {"column": "vi_tri_id", "ref_table": "vi_tri", "ref_column": "vi_tri_id"},
        ],
    },
    {
        "name": "nha_phan_phoi_khach_hang",
        "description": "Quan hệ giữa nhà phân phối và khách hàng, bao gồm nhân viên và tuyến hiện tại.",
        "aliases": ["distributor customer", "customer mapping", "khach hang cua nha phan phoi", "current route"],
        "columns": [
            {"name": "phan_phoi_khach_hang_id", "type": "INTEGER", "pk": True, "desc": "Mã quan hệ phân phối khách hàng."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối."},
            {"name": "khach_hang_id", "type": "TEXT", "desc": "Mã khách hàng."},
            {"name": "nhan_vien_id", "type": "TEXT", "desc": "Mã nhân viên hiện tại phụ trách khách hàng."},
            {"name": "tuyen_id", "type": "INTEGER", "desc": "Mã tuyến hiện tại của khách hàng."},
            {"name": "ngay_mo", "type": "TEXT", "desc": "Ngày mở quan hệ."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái OPEN hoặc CLOSED."},
        ],
        "foreign_keys": [
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"},
            {"column": "khach_hang_id", "ref_table": "khach_hang", "ref_column": "khach_hang_id"},
            {"column": "nhan_vien_id", "ref_table": "nhan_vien", "ref_column": "nhan_vien_id"},
            {"column": "tuyen_id", "ref_table": "tuyen_ban_hang", "ref_column": "tuyen_id"},
        ],
    },
    {
        "name": "danh_muc_san_pham",
        "description": "Danh mục sản phẩm như đồ uống, bánh kẹo, sữa, gia dụng.",
        "aliases": ["category", "danh muc", "product category", "sales by category"],
        "columns": [
            {"name": "danh_muc_id", "type": "TEXT", "pk": True, "desc": "Mã danh mục sản phẩm."},
            {"name": "ten_danh_muc", "type": "TEXT", "desc": "Tên danh mục sản phẩm."},
        ],
        "foreign_keys": [],
    },
    {
        "name": "san_pham",
        "description": "Sản phẩm hoặc SKU có thể bán, thuộc công ty và được nhóm theo danh mục.",
        "aliases": ["product", "sku", "san pham", "item", "product sales", "units sold"],
        "columns": [
            {"name": "san_pham_id", "type": "TEXT", "pk": True, "desc": "Mã sản phẩm."},
            {"name": "cong_ty_id", "type": "TEXT", "desc": "Mã công ty sở hữu sản phẩm."},
            {"name": "danh_muc_id", "type": "TEXT", "desc": "Mã danh mục của sản phẩm."},
            {"name": "ten_san_pham", "type": "TEXT", "desc": "Tên sản phẩm."},
            {"name": "don_vi_tinh", "type": "TEXT", "desc": "Đơn vị tính."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái ACTIVE hoặc INACTIVE."},
        ],
        "foreign_keys": [
            {"column": "cong_ty_id", "ref_table": "cong_ty", "ref_column": "cong_ty_id"},
            {"column": "danh_muc_id", "ref_table": "danh_muc_san_pham", "ref_column": "danh_muc_id"},
        ],
    },
    {
        "name": "bang_gia_san_pham",
        "description": "Bảng giá sản phẩm có hiệu lực trong một khoảng thời gian.",
        "aliases": ["price list", "bang gia", "gia san pham", "unit price"],
        "columns": [
            {"name": "bang_gia_id", "type": "INTEGER", "pk": True, "desc": "Mã dòng bảng giá."},
            {"name": "san_pham_id", "type": "TEXT", "desc": "Mã sản phẩm."},
            {"name": "gia_ban", "type": "REAL", "desc": "Giá bán."},
            {"name": "ngay_bat_dau", "type": "TEXT", "desc": "Ngày bắt đầu hiệu lực."},
            {"name": "ngay_ket_thuc", "type": "TEXT", "desc": "Ngày kết thúc hiệu lực."},
        ],
        "foreign_keys": [
            {"column": "san_pham_id", "ref_table": "san_pham", "ref_column": "san_pham_id"}
        ],
    },
    {
        "name": "khuyen_mai",
        "description": "Chương trình khuyến mãi có khoảng ngày hiệu lực và phần trăm giảm giá.",
        "aliases": ["promotion", "discount", "khuyen mai", "campaign"],
        "columns": [
            {"name": "khuyen_mai_id", "type": "TEXT", "pk": True, "desc": "Mã khuyến mãi."},
            {"name": "cong_ty_id", "type": "TEXT", "desc": "Mã công ty áp dụng khuyến mãi."},
            {"name": "ten_khuyen_mai", "type": "TEXT", "desc": "Tên chương trình khuyến mãi."},
            {"name": "phan_tram_giam", "type": "REAL", "desc": "Phần trăm giảm giá."},
            {"name": "ngay_bat_dau", "type": "TEXT", "desc": "Ngày bắt đầu khuyến mãi."},
            {"name": "ngay_ket_thuc", "type": "TEXT", "desc": "Ngày kết thúc khuyến mãi."},
        ],
        "foreign_keys": [
            {"column": "cong_ty_id", "ref_table": "cong_ty", "ref_column": "cong_ty_id"}
        ],
    },
    {
        "name": "khuyen_mai_san_pham",
        "description": "Quan hệ nhiều-nhiều giữa chương trình khuyến mãi và sản phẩm.",
        "aliases": ["promotion products", "san pham khuyen mai", "discounted sku"],
        "columns": [
            {"name": "khuyen_mai_san_pham_id", "type": "INTEGER", "pk": True, "desc": "Mã quan hệ khuyến mãi sản phẩm."},
            {"name": "khuyen_mai_id", "type": "TEXT", "desc": "Mã khuyến mãi."},
            {"name": "san_pham_id", "type": "TEXT", "desc": "Mã sản phẩm."},
        ],
        "foreign_keys": [
            {"column": "khuyen_mai_id", "ref_table": "khuyen_mai", "ref_column": "khuyen_mai_id"},
            {"column": "san_pham_id", "ref_table": "san_pham", "ref_column": "san_pham_id"},
        ],
    },
    {
        "name": "lich_su_vieng_tham",
        "description": "Lịch sử viếng thăm khách hàng theo nhân viên, nhà phân phối, tuyến và kết quả viếng thăm.",
        "aliases": [
            "visit",
            "customer visit",
            "lich su vieng tham",
            "vieng tham khach hang",
            "visit result",
            "no order visit",
            "ordered visit",
        ],
        "columns": [
            {"name": "vieng_tham_id", "type": "INTEGER", "pk": True, "desc": "Mã lượt viếng thăm."},
            {"name": "khach_hang_id", "type": "TEXT", "desc": "Mã khách hàng được viếng thăm."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối."},
            {"name": "nhan_vien_id", "type": "TEXT", "desc": "Mã nhân viên viếng thăm."},
            {"name": "tuyen_id", "type": "INTEGER", "desc": "Mã tuyến bán hàng."},
            {"name": "ngay_vieng_tham", "type": "TEXT", "desc": "Ngày viếng thăm."},
            {"name": "ket_qua", "type": "TEXT", "desc": "Kết quả viếng thăm: VISITED, ORDERED, NO_ORDER, STORE_CLOSED, CUSTOMER_BUSY, NOT_FOUND."},
            {"name": "ghi_chu", "type": "TEXT", "desc": "Ghi chú viếng thăm."},
        ],
        "foreign_keys": [
            {"column": "khach_hang_id", "ref_table": "khach_hang", "ref_column": "khach_hang_id"},
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"},
            {"column": "nhan_vien_id", "ref_table": "nhan_vien", "ref_column": "nhan_vien_id"},
            {"column": "tuyen_id", "ref_table": "tuyen_ban_hang", "ref_column": "tuyen_id"},
        ],
    },
    {
        "name": "don_hang_ban",
        "description": "Đơn hàng bán do nhân viên tạo cho khách hàng, thường liên kết với một lượt viếng thăm.",
        "aliases": [
            "sales order",
            "don hang",
            "don hang ban",
            "order",
            "revenue",
            "sales amount",
            "falling order frequency",
        ],
        "columns": [
            {"name": "don_hang_id", "type": "TEXT", "pk": True, "desc": "Mã đơn hàng bán."},
            {"name": "cong_ty_id", "type": "TEXT", "desc": "Mã công ty."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối."},
            {"name": "nhan_vien_id", "type": "TEXT", "desc": "Mã nhân viên tạo đơn."},
            {"name": "khach_hang_id", "type": "TEXT", "desc": "Mã khách hàng đặt hàng."},
            {"name": "vieng_tham_id", "type": "INTEGER", "desc": "Mã lượt viếng thăm phát sinh đơn hàng."},
            {"name": "ngay_dat_hang", "type": "TEXT", "desc": "Ngày đặt hàng dùng để phân tích tần suất và xu hướng đơn hàng."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái NORMAL hoặc CANCELLED."},
            {"name": "tong_tien", "type": "REAL", "desc": "Tổng tiền đơn hàng."},
        ],
        "foreign_keys": [
            {"column": "cong_ty_id", "ref_table": "cong_ty", "ref_column": "cong_ty_id"},
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"},
            {"column": "nhan_vien_id", "ref_table": "nhan_vien", "ref_column": "nhan_vien_id"},
            {"column": "khach_hang_id", "ref_table": "khach_hang", "ref_column": "khach_hang_id"},
            {"column": "vieng_tham_id", "ref_table": "lich_su_vieng_tham", "ref_column": "vieng_tham_id"},
        ],
    },
    {
        "name": "chi_tiet_don_hang_ban",
        "description": "Chi tiết dòng hàng trong đơn bán gồm sản phẩm, số lượng, giá, khuyến mãi và thành tiền.",
        "aliases": ["order item", "sales line", "chi tiet don hang", "product revenue", "units sold"],
        "columns": [
            {"name": "chi_tiet_id", "type": "INTEGER", "pk": True, "desc": "Mã chi tiết đơn hàng."},
            {"name": "don_hang_id", "type": "TEXT", "desc": "Mã đơn hàng bán."},
            {"name": "san_pham_id", "type": "TEXT", "desc": "Mã sản phẩm."},
            {"name": "khuyen_mai_id", "type": "TEXT", "desc": "Mã khuyến mãi, có thể để trống."},
            {"name": "so_luong", "type": "INTEGER", "desc": "Số lượng đặt hàng."},
            {"name": "don_gia", "type": "REAL", "desc": "Đơn giá bán."},
            {"name": "thanh_tien", "type": "REAL", "desc": "Thành tiền của dòng hàng sau khi áp dụng giảm giá."},
        ],
        "foreign_keys": [
            {"column": "don_hang_id", "ref_table": "don_hang_ban", "ref_column": "don_hang_id"},
            {"column": "san_pham_id", "ref_table": "san_pham", "ref_column": "san_pham_id"},
            {"column": "khuyen_mai_id", "ref_table": "khuyen_mai", "ref_column": "khuyen_mai_id"},
        ],
    },
    {
        "name": "don_giao_hang",
        "description": "Đơn giao hàng cho đơn bán, bao gồm ngày xuất kho và ngày giao.",
        "aliases": ["delivery", "shipment", "don giao hang", "delivered order"],
        "columns": [
            {"name": "giao_hang_id", "type": "INTEGER", "pk": True, "desc": "Mã giao hàng."},
            {"name": "don_hang_id", "type": "TEXT", "desc": "Mã đơn hàng bán."},
            {"name": "nha_phan_phoi_id", "type": "TEXT", "desc": "Mã nhà phân phối giao hàng."},
            {"name": "ngay_xuat_kho", "type": "TEXT", "desc": "Ngày xuất kho."},
            {"name": "ngay_giao", "type": "TEXT", "desc": "Ngày giao hàng."},
            {"name": "trang_thai", "type": "TEXT", "desc": "Trạng thái SHIPPED, DELIVERED hoặc FAILED."},
        ],
        "foreign_keys": [
            {"column": "don_hang_id", "ref_table": "don_hang_ban", "ref_column": "don_hang_id"},
            {"column": "nha_phan_phoi_id", "ref_table": "nha_phan_phoi", "ref_column": "nha_phan_phoi_id"},
        ],
    },
    {
        "name": "hang_tra_ve",
        "description": "Dòng hàng trả về gắn với đơn hàng bán gốc và sản phẩm.",
        "aliases": ["return", "refund", "hang tra ve", "returned product", "damaged goods"],
        "columns": [
            {"name": "tra_ve_id", "type": "INTEGER", "pk": True, "desc": "Mã hàng trả về."},
            {"name": "don_hang_id", "type": "TEXT", "desc": "Mã đơn hàng bán gốc."},
            {"name": "san_pham_id", "type": "TEXT", "desc": "Mã sản phẩm bị trả về."},
            {"name": "ngay_tra", "type": "TEXT", "desc": "Ngày trả hàng."},
            {"name": "so_luong", "type": "INTEGER", "desc": "Số lượng trả về."},
            {"name": "ly_do", "type": "TEXT", "desc": "Lý do trả hàng."},
        ],
        "foreign_keys": [
            {"column": "don_hang_id", "ref_table": "don_hang_ban", "ref_column": "don_hang_id"},
            {"column": "san_pham_id", "ref_table": "san_pham", "ref_column": "san_pham_id"},
        ],
    },
]

_BY_NAME: Dict[str, dict] = {t["name"]: t for t in TABLES}


def all_table_names() -> List[str]:
    return [t["name"] for t in TABLES]


def get_table(name: str) -> dict:
    return _BY_NAME[name]


def columns_of(name: str) -> List[str]:
    return [c["name"] for c in _BY_NAME[name]["columns"]]


def primary_key(name: str) -> str:
    for c in _BY_NAME[name]["columns"]:
        if c.get("pk"):
            return c["name"]
    return columns_of(name)[0]


def all_foreign_keys() -> List[dict]:
    out: List[dict] = []
    for t in TABLES:
        for fk in t["foreign_keys"]:
            out.append(
                {
                    "from_table": t["name"],
                    "from_column": fk["column"],
                    "to_table": fk["ref_table"],
                    "to_column": fk["ref_column"],
                }
            )
    return out


def ddl_for(name: str) -> str:
    t = _BY_NAME[name]
    lines = [f"CREATE TABLE {name} ("]
    col_lines = []
    for c in t["columns"]:
        decl = f"    {c['name']} {c['type']}"
        if c.get("pk"):
            decl += " PRIMARY KEY"
        col_lines.append(decl)
    for fk in t["foreign_keys"]:
        col_lines.append(
            f"    FOREIGN KEY ({fk['column']}) REFERENCES {fk['ref_table']}({fk['ref_column']})"
        )
    lines.append(",\n".join(col_lines))
    lines.append(");")
    return "\n".join(lines)
