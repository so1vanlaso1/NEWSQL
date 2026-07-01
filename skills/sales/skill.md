# Database Skill: FMCG Sales Database (SQLNEW)

## SQL Dialect

SQLite. Use SQLite functions (date(), strftime('%Y-%m', col)). Do NOT use MySQL functions like DATE_FORMAT/CURDATE/DATE_ADD.

## Global SQL Rules

- Use only the provided tables, columns, and joins.
- Do not invent tables, columns, or join conditions.
- Use exact table and column names.
- Return one executable SQLite SELECT query when SQL is needed.
- SELECT only: never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or CREATE.
- Database identifiers use Vietnamese không dấu with snake_case; user questions may be có dấu.
- For revenue / doanh thu / doanh so / sales use the provided doanh_thu formula (net, thanh_tien).
- Add a LIMIT for exploratory / top-N questions.

## Vietnamese Normalization Rules

- công ty -> cong_ty
- khách hàng -> khach_hang
- nhà phân phối -> nha_phan_phoi
- nhân viên -> nhan_vien
- đơn hàng bán -> don_hang_ban
- chi tiết đơn hàng bán -> chi_tiet_don_hang_ban
- sản phẩm -> san_pham
- danh mục -> danh_muc_san_pham
- khuyến mãi -> khuyen_mai
- viếng thăm -> lich_su_vieng_tham
- tỉnh thành / thành phố -> vi_tri.tinh_thanh
- vùng / miền -> vung.ten_vung
- doanh thu / doanh số -> doanh_thu
- ngày đặt hàng -> don_hang_ban.ngay_dat_hang

## Data Coverage

The database only holds orders from 2024-01-01 to 2025-06-21. date('now') is outside this window, so relative periods like "this month" return nothing -- anchor recent-period questions to MAX(ngay_dat_hang) or an explicit date in range.

## Revenue & Metric Policy

Canonical doanh_thu = SUM(chi_tiet_don_hang_ban.thanh_tien) (net, after promotions), over NORMAL orders. Gross (pre-discount) = SUM(so_luong * don_gia). Order-header total = SUM(don_hang_ban.tong_tien) over DISTINCT don_hang_id. City/province filters (e.g. Ho Chi Minh) go through khach_hang.vi_tri_id -> vi_tri.tinh_thanh.

---

# Metrics

## Metric: doanh_thu

Aliases: doanh thu, doanh số, doanh so, sales, revenue, tổng tiền bán hàng, tong tien ban hang, net sales, doanh thu thuần

Formula:
```sql
SUM(chi_tiet_don_hang_ban.thanh_tien)
```
Required tables: don_hang_ban, chi_tiet_don_hang_ban
Required join: don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id
Use when: user asks about revenue / doanh thu / doanh so / sales amount / total money from orders (net, after promotions)
Notes: Canonical net revenue using thanh_tien (already discounted). Count only realized orders with don_hang_ban.trang_thai = 'NORMAL'. Gross (pre-discount) = SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia). Order-header total = SUM(don_hang_ban.tong_tien) over DISTINCT don_hang_id (do not sum tong_tien after joining line items -- it double counts).

## Metric: doanh_thu_gross

Aliases: doanh thu gộp, doanh thu goc, gross revenue, revenue before discount, doanh thu trước giảm giá, gia tri niem yet

Formula:
```sql
SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia)
```
Required tables: don_hang_ban, chi_tiet_don_hang_ban
Required join: don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id
Use when: user explicitly asks revenue before discount / list-price value (not the default revenue)
Notes: Pre-discount gross. Default revenue questions should use doanh_thu (net) instead.

## Metric: so_don_hang

Aliases: số đơn hàng, so don hang, number of orders, order count, số lượng đơn

Formula:
```sql
COUNT(DISTINCT don_hang_ban.don_hang_id)
```
Required tables: don_hang_ban
Use when: user asks how many orders / order count / order frequency
Notes: Use DISTINCT don_hang_id when the query also joins chi_tiet_don_hang_ban.

## Metric: so_khach_hang

Aliases: số khách hàng, so khach hang, number of customers, distinct customers, khách mua

Formula:
```sql
COUNT(DISTINCT don_hang_ban.khach_hang_id)
```
Required tables: don_hang_ban
Use when: user asks how many distinct customers ordered / number of buying outlets
Notes: For all registered customers (not just buyers) use COUNT(*) on khach_hang.

## Metric: so_luong_ban

Aliases: số lượng bán, so luong ban, units sold, quantity sold, sản lượng, san luong

Formula:
```sql
SUM(chi_tiet_don_hang_ban.so_luong)
```
Required tables: chi_tiet_don_hang_ban
Use when: user asks about quantity / units / số lượng bán / sản lượng of products
Notes: Join to don_hang_ban only when filtering by order attributes (date, customer, status).

## Metric: so_luong_tra

Aliases: số lượng trả về, so luong tra, returned units, hàng trả về, return quantity

Formula:
```sql
SUM(hang_tra_ve.so_luong)
```
Required tables: hang_tra_ve
Use when: user asks about returned quantity / returns / hàng trả về
Notes: Return rate = returned units / sold units. Compute the two sums in separate subqueries (one over hang_tra_ve, one over chi_tiet_don_hang_ban); do not join the two fact tables directly or the counts multiply.

## Metric: ty_le_vieng_tham_thanh_cong

Aliases: tỷ lệ viếng thăm thành công, ty le vieng tham thanh cong, visit success rate, tỷ lệ chốt đơn, ordered visit rate, conversion rate

Formula:
```sql
SUM(CASE WHEN lich_su_vieng_tham.ket_qua = 'ORDERED' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)
```
Required tables: lich_su_vieng_tham
Use when: user asks about visit success / conversion of visits into orders / ordered rate
Notes: ket_qua = 'ORDERED' marks a visit that produced an order.

---

# Join Paths

## Join Path: returns_by_product

Use when: hàng trả về theo sản phẩm / most returned products
Required tables: hang_tra_ve, san_pham
Joins:
- san_pham.san_pham_id = hang_tra_ve.san_pham_id

## Join Path: revenue_by_category

Use when: doanh thu theo danh mục / sales by category (đồ uống, bánh kẹo, sữa, ...)
Required tables: danh_muc_san_pham, san_pham, chi_tiet_don_hang_ban, don_hang_ban
Joins:
- danh_muc_san_pham.danh_muc_id = san_pham.danh_muc_id
- san_pham.san_pham_id = chi_tiet_don_hang_ban.san_pham_id
- chi_tiet_don_hang_ban.don_hang_id = don_hang_ban.don_hang_id

## Join Path: revenue_by_company

Use when: doanh thu theo công ty / sales by company / revenue by brand owner
Required tables: cong_ty, don_hang_ban, chi_tiet_don_hang_ban
Joins:
- cong_ty.cong_ty_id = don_hang_ban.cong_ty_id
- don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id

## Join Path: revenue_by_customer

Use when: doanh thu theo khách hàng / top customers by revenue
Required tables: khach_hang, don_hang_ban, chi_tiet_don_hang_ban
Joins:
- khach_hang.khach_hang_id = don_hang_ban.khach_hang_id
- don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id

## Join Path: revenue_by_distributor

Use when: doanh thu theo nhà phân phối / sales by distributor / NPP performance
Required tables: nha_phan_phoi, don_hang_ban, chi_tiet_don_hang_ban
Joins:
- nha_phan_phoi.nha_phan_phoi_id = don_hang_ban.nha_phan_phoi_id
- don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id

## Join Path: revenue_by_product

Use when: doanh thu theo sản phẩm / best selling products / product revenue / units sold by product
Required tables: san_pham, chi_tiet_don_hang_ban, don_hang_ban
Joins:
- san_pham.san_pham_id = chi_tiet_don_hang_ban.san_pham_id
- chi_tiet_don_hang_ban.don_hang_id = don_hang_ban.don_hang_id

## Join Path: revenue_by_province

Use when: doanh thu theo tỉnh thành / sales by province or city (e.g. HCM, Ha Noi, Da Nang)
Required tables: vi_tri, khach_hang, don_hang_ban, chi_tiet_don_hang_ban
Joins:
- vi_tri.vi_tri_id = khach_hang.vi_tri_id
- khach_hang.khach_hang_id = don_hang_ban.khach_hang_id
- don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id

## Join Path: revenue_by_region

Use when: doanh thu theo vùng / sales by region (miền Bắc, miền Trung, miền Nam, ...)
Required tables: vung, nha_phan_phoi, don_hang_ban, chi_tiet_don_hang_ban
Joins:
- vung.vung_id = nha_phan_phoi.vung_id
- nha_phan_phoi.nha_phan_phoi_id = don_hang_ban.nha_phan_phoi_id
- don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id

## Join Path: sales_by_staff

Use when: doanh thu theo nhân viên / sales performance by salesperson
Required tables: nhan_vien, don_hang_ban, chi_tiet_don_hang_ban
Joins:
- nhan_vien.nhan_vien_id = don_hang_ban.nhan_vien_id
- don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id

## Join Path: visits_by_customer

Use when: viếng thăm theo khách hàng / visit history and results per outlet
Required tables: lich_su_vieng_tham, khach_hang
Joins:
- khach_hang.khach_hang_id = lich_su_vieng_tham.khach_hang_id

---

# Tables

## Table: cong_ty

### Business meaning
Công ty FMCG hoặc chủ thương hiệu bán sản phẩm thông qua nhà phân phối.
FMCG company / brand owner / supplier.

### Use this table when
- company
- brand owner
- supplier
- công ty
- doanh nghiệp
- sản phẩm/doanh thu theo công ty

### Do not use this table alone when
- revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban

### Primary key
cong_ty_id

### Columns
- cong_ty_id (TEXT): Mã công ty.
- ten_cong_ty (TEXT): Tên công ty.
- nganh_hang (TEXT): Ngành hàng kinh doanh như FMCG hoặc đồ uống.

### Allowed joins
- nha_phan_phoi.cong_ty_id = cong_ty.cong_ty_id
- san_pham.cong_ty_id = cong_ty.cong_ty_id
- khuyen_mai.cong_ty_id = cong_ty.cong_ty_id
- don_hang_ban.cong_ty_id = cong_ty.cong_ty_id

### Common values
- ten_cong_ty: Nuoc Giai Khat Sao Viet, Cong ty FMCG An Phat
- nganh_hang: FMCG, Beverage

### Retrieval text
cong_ty: FMCG company / brand owner / supplier. Aliases: company, brand owner, cong ty, doanh nghiep.

## Table: vung

### Business meaning
Vùng bán hàng tại Việt Nam như miền Bắc, miền Trung, miền Nam.
Sales region/territory in Vietnam (North, Central, South, ...).

### Use this table when
- region
- territory
- miền
- vùng
- doanh thu theo vùng

### Do not use this table alone when
- revenue alone -- reach orders via nha_phan_phoi

### Primary key
vung_id

### Columns
- vung_id (TEXT): Mã vùng.
- ten_vung (TEXT): Region name (Mien Bac, Mien Trung, Mien Nam, Tay Nguyen, Mekong).
- quoc_gia (TEXT): Tên quốc gia.

### Allowed joins
- nha_phan_phoi.vung_id = vung.vung_id
- tuyen_ban_hang.vung_id = vung.vung_id

### Common values
- ten_vung: Tay Nguyen, Mien Trung, Mien Nam, Mien Bac, Mekong
- quoc_gia: Viet Nam

### Retrieval text
vung: Sales region/territory in Vietnam (North, Central, South, ...). Aliases: region, territory, mien, khu vuc, vung ban hang.

## Table: nha_phan_phoi

### Business meaning
Nhà phân phối phục vụ khách hàng, tuyến, nhân viên, viếng thăm và đơn hàng trong một vùng.
Distributor serving customers, routes, staff and orders in a region.

### Use this table when
- distributor
- NPP
- nhà phân phối
- doanh thu theo nhà phân phối

### Do not use this table alone when
- revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban

### Primary key
nha_phan_phoi_id

### Columns
- nha_phan_phoi_id (TEXT): Mã nhà phân phối.
- cong_ty_id (TEXT): Mã công ty mà nhà phân phối trực thuộc.
- vung_id (TEXT): Mã vùng hoạt động của nhà phân phối.
- ten_nha_phan_phoi (TEXT): Tên nhà phân phối.
- trang_thai (TEXT): Distributor status: ACTIVE or INACTIVE.

### Allowed joins
- nha_phan_phoi.cong_ty_id = cong_ty.cong_ty_id
- nha_phan_phoi.vung_id = vung.vung_id
- tuyen_ban_hang.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- nhan_vien.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- phan_cong_tuyen.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- nha_phan_phoi_khach_hang.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- lich_su_vieng_tham.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- don_hang_ban.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- don_giao_hang.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id

### Common values
- ten_nha_phan_phoi: Nha phan phoi H, Nha phan phoi G, Nha phan phoi F, Nha phan phoi E, Nha phan phoi D
- trang_thai: ACTIVE, INACTIVE

### Retrieval text
nha_phan_phoi: Distributor serving customers, routes, staff and orders in a region. Aliases: distributor, nha phan phoi, NPP, wholesale partner, sales by distributor, customers by distributor.

## Table: vi_tri

### Business meaning
Vị trí địa lý ở cấp tỉnh thành, quận huyện, phường xã và tọa độ.
Geographic location: province/city, district, ward, and coordinates.

### Use this table when
- province
- city
- tỉnh thành
- quận huyện
- HCM / Ha Noi / Da Nang
- doanh thu theo tỉnh

### Do not use this table alone when
- revenue alone -- join via khach_hang then don_hang_ban

### Primary key
vi_tri_id

### Columns
- vi_tri_id (TEXT): Mã vị trí.
- tinh_thanh (TEXT): Province or city name (e.g. Ho Chi Minh, Ha Noi, Da Nang).
- quan_huyen (TEXT): Quận hoặc huyện.
- phuong_xa (TEXT): Phường hoặc xã.
- vi_do (REAL): Vĩ độ.
- kinh_do (REAL): Kinh độ.

### Allowed joins
- tuyen_ban_hang.vi_tri_id = vi_tri.vi_tri_id
- khach_hang.vi_tri_id = vi_tri.vi_tri_id

### Common values
- tinh_thanh: An Giang, Can Tho, Da Nang, Dong Nai, Ha Noi
- quan_huyen: Vinh, Quan 7, Ninh Kieu, Nha Trang, Long Xuyen
- phuong_xa: Tan Phong, Tan Hiep, Phuong 1, Niem Nghia, My Binh

### Retrieval text
vi_tri: Geographic location: province/city, district, ward, and coordinates. Aliases: location, dia diem, vi tri, province, district, ward, toa do.

## Table: tuyen_ban_hang

### Business meaning
Tuyến bán hàng thuộc một nhà phân phối và gắn với vùng cùng vị trí.
Sales route owned by a distributor, tied to a region and location.

### Use this table when
- route
- tuyến bán hàng
- visit route
- delivery route

### Primary key
tuyen_id

### Columns
- tuyen_id (INTEGER): Mã tuyến bán hàng.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối sở hữu tuyến.
- vung_id (TEXT): Mã vùng của tuyến.
- vi_tri_id (TEXT): Mã vị trí chính của tuyến.
- ma_tuyen (TEXT): Mã định danh tuyến.
- ten_tuyen (TEXT): Tên tuyến bán hàng.
- trang_thai (TEXT): Trạng thái ACTIVE hoặc INACTIVE.

### Allowed joins
- tuyen_ban_hang.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- tuyen_ban_hang.vung_id = vung.vung_id
- tuyen_ban_hang.vi_tri_id = vi_tri.vi_tri_id
- phan_cong_tuyen.tuyen_id = tuyen_ban_hang.tuyen_id
- nha_phan_phoi_khach_hang.tuyen_id = tuyen_ban_hang.tuyen_id
- lich_su_vieng_tham.tuyen_id = tuyen_ban_hang.tuyen_id

### Common values
- ma_tuyen: T021, T020, T019, T018, T017
- ten_tuyen: Tuyen Quan 7 1, Tuyen Ninh Kieu 1, Tuyen Nha Trang 2, Tuyen Vinh 3, Tuyen Vinh 2
- trang_thai: ACTIVE

### Retrieval text
tuyen_ban_hang: Sales route owned by a distributor, tied to a region and location. Aliases: route, sales route, tuyen, routing, visit route, delivery route.

## Table: nhan_vien

### Business meaning
Nhân viên bán hàng viếng thăm khách hàng và tạo đơn hàng cho nhà phân phối.
Sales staff who visit customers and create orders.

### Use this table when
- staff
- salesperson
- nhân viên
- sales performance
- doanh thu theo nhân viên

### Do not use this table alone when
- revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban

### Primary key
nhan_vien_id

### Columns
- nhan_vien_id (TEXT): Mã nhân viên.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối tuyển dụng nhân viên.
- ten_nhan_vien (TEXT): Tên nhân viên.
- ngay_vao_lam (TEXT): Ngày vào làm.
- trang_thai (TEXT): Trạng thái ACTIVE hoặc INACTIVE.

### Allowed joins
- nhan_vien.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- phan_cong_tuyen.nhan_vien_id = nhan_vien.nhan_vien_id
- nha_phan_phoi_khach_hang.nhan_vien_id = nhan_vien.nhan_vien_id
- lich_su_vieng_tham.nhan_vien_id = nhan_vien.nhan_vien_id
- don_hang_ban.nhan_vien_id = nhan_vien.nhan_vien_id

### Common values
- ten_nhan_vien: Nhan vien ban hang 9, Nhan vien ban hang 8, Nhan vien ban hang 7, Nhan vien ban hang 6, Nhan vien ban hang 5
- trang_thai: ACTIVE

### Retrieval text
nhan_vien: Sales staff who visit customers and create orders. Aliases: staff, salesperson, sales rep, nhan vien, trinh duoc vien, sales performance.

## Table: phan_cong_tuyen

### Business meaning
Phân công nhân viên phụ trách tuyến bán hàng trong một khoảng thời gian.
Assignment of staff to a route for a time period.

### Use this table when
- route assignment
- phân công tuyến
- who covers which route

### Primary key
phan_cong_id

### Columns
- phan_cong_id (INTEGER): Mã phân công.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối.
- nhan_vien_id (TEXT): Mã nhân viên được phân công.
- tuyen_id (INTEGER): Mã tuyến được phân công.
- ngay_bat_dau (TEXT): Ngày bắt đầu phân công.
- ngay_ket_thuc (TEXT): Ngày kết thúc, có thể để trống.

### Allowed joins
- phan_cong_tuyen.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- phan_cong_tuyen.nhan_vien_id = nhan_vien.nhan_vien_id
- phan_cong_tuyen.tuyen_id = tuyen_ban_hang.tuyen_id

### Retrieval text
phan_cong_tuyen: Assignment of staff to a route for a time period. Aliases: route assignment, phan cong tuyen, staff route, assigned route.

## Table: loai_khach_hang

### Business meaning
Loại điểm bán hoặc khách hàng như tạp hóa, siêu thị mini, đại lý sỉ.
Customer/outlet type (grocery, mini-supermarket, wholesale, ...).

### Use this table when
- customer type
- channel
- loại khách hàng
- kênh bán

### Primary key
loai_khach_hang_id

### Columns
- loai_khach_hang_id (TEXT): Mã loại khách hàng.
- ten_loai (TEXT): Tên loại khách hàng.
- mo_ta (TEXT): Mô tả loại khách hàng.

### Allowed joins
- khach_hang.loai_khach_hang_id = loai_khach_hang.loai_khach_hang_id

### Common values
- ten_loai: Tap hoa, Sieu thi mini, Nha hang khach san, Dai ly si, Cua hang tien loi
- mo_ta: Wholesale shop, Traditional grocery outlet, Mini supermarket, Hotel restaurant cafe, Convenience store

### Retrieval text
loai_khach_hang: Customer/outlet type (grocery, mini-supermarket, wholesale, ...). Aliases: customer type, loai khach hang, channel, outlet type.

## Table: khach_hang

### Business meaning
Khách hàng bán lẻ hoặc điểm bán được nhân viên viếng thăm và nhà phân phối phục vụ.
Retail customer / outlet visited by staff and served by a distributor.

### Use this table when
- customer
- outlet
- shop
- khách hàng
- điểm bán
- top khách hàng

### Do not use this table alone when
- revenue alone -- join to don_hang_ban + chi_tiet_don_hang_ban

### Primary key
khach_hang_id

### Columns
- khach_hang_id (TEXT): Mã khách hàng.
- loai_khach_hang_id (TEXT): Mã loại khách hàng.
- vi_tri_id (TEXT): Mã vị trí của khách hàng.
- ten_khach_hang (TEXT): Customer / outlet name.
- dia_chi (TEXT): Địa chỉ.
- so_dien_thoai (TEXT): Số điện thoại.
- ngay_tao (TEXT): Ngày tạo khách hàng.

### Allowed joins
- khach_hang.loai_khach_hang_id = loai_khach_hang.loai_khach_hang_id
- khach_hang.vi_tri_id = vi_tri.vi_tri_id
- nha_phan_phoi_khach_hang.khach_hang_id = khach_hang.khach_hang_id
- lich_su_vieng_tham.khach_hang_id = khach_hang.khach_hang_id
- don_hang_ban.khach_hang_id = khach_hang.khach_hang_id

### Common values
- ten_khach_hang: Cua hang 99, Cua hang 98, Cua hang 97, Cua hang 96, Cua hang 95

### Retrieval text
khach_hang: Retail customer / outlet visited by staff and served by a distributor. Aliases: customer, khach hang, outlet, shop, retail store, visit customer, customer order frequency.

## Table: nha_phan_phoi_khach_hang

### Business meaning
Quan hệ giữa nhà phân phối và khách hàng, bao gồm nhân viên và tuyến hiện tại.
Distributor-customer relationship with current staff and route.

### Use this table when
- which distributor serves a customer
- current route of a customer
- customer mapping

### Primary key
phan_phoi_khach_hang_id

### Columns
- phan_phoi_khach_hang_id (INTEGER): Mã quan hệ phân phối khách hàng.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối.
- khach_hang_id (TEXT): Mã khách hàng.
- nhan_vien_id (TEXT): Mã nhân viên hiện tại phụ trách khách hàng.
- tuyen_id (INTEGER): Mã tuyến hiện tại của khách hàng.
- ngay_mo (TEXT): Ngày mở quan hệ.
- trang_thai (TEXT): Trạng thái OPEN hoặc CLOSED.

### Allowed joins
- nha_phan_phoi_khach_hang.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- nha_phan_phoi_khach_hang.khach_hang_id = khach_hang.khach_hang_id
- nha_phan_phoi_khach_hang.nhan_vien_id = nhan_vien.nhan_vien_id
- nha_phan_phoi_khach_hang.tuyen_id = tuyen_ban_hang.tuyen_id

### Common values
- trang_thai: OPEN

### Retrieval text
nha_phan_phoi_khach_hang: Distributor-customer relationship with current staff and route. Aliases: distributor customer, customer mapping, khach hang cua nha phan phoi, current route.

## Table: danh_muc_san_pham

### Business meaning
Danh mục sản phẩm như đồ uống, bánh kẹo, sữa, gia dụng.
Product category (beverages, snacks, dairy, home, care, frozen).

### Use this table when
- category
- danh mục
- doanh thu theo danh mục

### Do not use this table alone when
- revenue alone -- join via san_pham then chi_tiet_don_hang_ban

### Primary key
danh_muc_id

### Columns
- danh_muc_id (TEXT): Mã danh mục sản phẩm.
- ten_danh_muc (TEXT): Tên danh mục sản phẩm.

### Allowed joins
- san_pham.danh_muc_id = danh_muc_san_pham.danh_muc_id

### Common values
- ten_danh_muc: Sua, Gia dung, Dong lanh, Do uong, Cham soc ca nhan

### Retrieval text
danh_muc_san_pham: Product category (beverages, snacks, dairy, home, care, frozen). Aliases: category, danh muc, product category, sales by category.

## Table: san_pham

### Business meaning
Sản phẩm hoặc SKU có thể bán, thuộc công ty và được nhóm theo danh mục.
Product / SKU owned by a company and grouped by category.

### Use this table when
- product
- SKU
- sản phẩm
- mặt hàng
- best selling products

### Do not use this table alone when
- revenue alone -- join to chi_tiet_don_hang_ban

### Primary key
san_pham_id

### Columns
- san_pham_id (TEXT): Mã sản phẩm.
- cong_ty_id (TEXT): Mã công ty sở hữu sản phẩm.
- danh_muc_id (TEXT): Mã danh mục của sản phẩm.
- ten_san_pham (TEXT): Product name.
- don_vi_tinh (TEXT): Đơn vị tính.
- trang_thai (TEXT): Product status: ACTIVE or INACTIVE.

### Allowed joins
- san_pham.cong_ty_id = cong_ty.cong_ty_id
- san_pham.danh_muc_id = danh_muc_san_pham.danh_muc_id
- bang_gia_san_pham.san_pham_id = san_pham.san_pham_id
- khuyen_mai_san_pham.san_pham_id = san_pham.san_pham_id
- chi_tiet_don_hang_ban.san_pham_id = san_pham.san_pham_id
- hang_tra_ve.san_pham_id = san_pham.san_pham_id

### Common values
- ten_san_pham: San pham FMCG 9, San pham FMCG 8, San pham FMCG 7, San pham FMCG 60, San pham FMCG 6
- don_vi_tinh: hop, goi, chai, thung
- trang_thai: ACTIVE

### Retrieval text
san_pham: Product / SKU owned by a company and grouped by category. Aliases: product, sku, san pham, item, product sales, units sold.

## Table: bang_gia_san_pham

### Business meaning
Bảng giá sản phẩm có hiệu lực trong một khoảng thời gian.
Product price list effective over a date range.

### Use this table when
- price
- unit price
- bảng giá
- giá bán tại thời điểm

### Primary key
bang_gia_id

### Columns
- bang_gia_id (INTEGER): Mã dòng bảng giá.
- san_pham_id (TEXT): Mã sản phẩm.
- gia_ban (REAL): Giá bán.
- ngay_bat_dau (TEXT): Ngày bắt đầu hiệu lực.
- ngay_ket_thuc (TEXT): Ngày kết thúc hiệu lực.

### Allowed joins
- bang_gia_san_pham.san_pham_id = san_pham.san_pham_id

### Retrieval text
bang_gia_san_pham: Product price list effective over a date range. Aliases: price list, bang gia, gia san pham, unit price.

## Table: khuyen_mai

### Business meaning
Chương trình khuyến mãi có khoảng ngày hiệu lực và phần trăm giảm giá.
Promotion campaign with a date range and discount percent.

### Use this table when
- promotion
- discount
- khuyến mãi
- chương trình giảm giá

### Primary key
khuyen_mai_id

### Columns
- khuyen_mai_id (TEXT): Mã khuyến mãi.
- cong_ty_id (TEXT): Mã công ty áp dụng khuyến mãi.
- ten_khuyen_mai (TEXT): Tên chương trình khuyến mãi.
- phan_tram_giam (REAL): Phần trăm giảm giá.
- ngay_bat_dau (TEXT): Ngày bắt đầu khuyến mãi.
- ngay_ket_thuc (TEXT): Ngày kết thúc khuyến mãi.

### Allowed joins
- khuyen_mai.cong_ty_id = cong_ty.cong_ty_id
- khuyen_mai_san_pham.khuyen_mai_id = khuyen_mai.khuyen_mai_id
- chi_tiet_don_hang_ban.khuyen_mai_id = khuyen_mai.khuyen_mai_id

### Common values
- ten_khuyen_mai: Khuyen mai quy 8, Khuyen mai quy 7, Khuyen mai quy 6, Khuyen mai quy 5, Khuyen mai quy 4

### Retrieval text
khuyen_mai: Promotion campaign with a date range and discount percent. Aliases: promotion, discount, khuyen mai, campaign.

## Table: khuyen_mai_san_pham

### Business meaning
Quan hệ nhiều-nhiều giữa chương trình khuyến mãi và sản phẩm.
Many-to-many between promotions and products.

### Use this table when
- which products are in a promotion
- sản phẩm khuyến mãi

### Primary key
khuyen_mai_san_pham_id

### Columns
- khuyen_mai_san_pham_id (INTEGER): Mã quan hệ khuyến mãi sản phẩm.
- khuyen_mai_id (TEXT): Mã khuyến mãi.
- san_pham_id (TEXT): Mã sản phẩm.

### Allowed joins
- khuyen_mai_san_pham.khuyen_mai_id = khuyen_mai.khuyen_mai_id
- khuyen_mai_san_pham.san_pham_id = san_pham.san_pham_id

### Retrieval text
khuyen_mai_san_pham: Many-to-many between promotions and products. Aliases: promotion products, san pham khuyen mai, discounted sku.

## Table: lich_su_vieng_tham

### Business meaning
Lịch sử viếng thăm khách hàng theo nhân viên, nhà phân phối, tuyến và kết quả viếng thăm.
Customer visit history by staff, with result and notes.

### Use this table when
- visit
- viếng thăm
- visit result
- ordered/no-order visits
- tỷ lệ chốt đơn

### Primary key
vieng_tham_id

### Columns
- vieng_tham_id (INTEGER): Mã lượt viếng thăm.
- khach_hang_id (TEXT): Mã khách hàng được viếng thăm.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối.
- nhan_vien_id (TEXT): Mã nhân viên viếng thăm.
- tuyen_id (INTEGER): Mã tuyến bán hàng.
- ngay_vieng_tham (TEXT): Visit date.
- ket_qua (TEXT): Visit result: VISITED, ORDERED, NO_ORDER, STORE_CLOSED, CUSTOMER_BUSY, NOT_FOUND.
- ghi_chu (TEXT): Ghi chú viếng thăm.

### Allowed joins
- lich_su_vieng_tham.khach_hang_id = khach_hang.khach_hang_id
- lich_su_vieng_tham.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- lich_su_vieng_tham.nhan_vien_id = nhan_vien.nhan_vien_id
- lich_su_vieng_tham.tuyen_id = tuyen_ban_hang.tuyen_id
- don_hang_ban.vieng_tham_id = lich_su_vieng_tham.vieng_tham_id

### Common values
- ket_qua: ORDERED, NO_ORDER, STORE_CLOSED, CUSTOMER_BUSY, VISITED

### Retrieval text
lich_su_vieng_tham: Customer visit history by staff, with result and notes. Aliases: visit, customer visit, lich su vieng tham, vieng tham khach hang, visit result, no order visit, ordered visit.

## Table: don_hang_ban

### Business meaning
Đơn hàng bán do nhân viên tạo cho khách hàng, thường liên kết với một lượt viếng thăm.
Sales order header created by staff for a customer, usually from a visit.

### Use this table when
- order
- đơn hàng
- revenue
- doanh thu
- order date
- order count

### Primary key
don_hang_id

### Columns
- don_hang_id (TEXT): Mã đơn hàng bán.
- cong_ty_id (TEXT): Mã công ty.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối.
- nhan_vien_id (TEXT): Mã nhân viên tạo đơn.
- khach_hang_id (TEXT): Mã khách hàng đặt hàng.
- vieng_tham_id (INTEGER): Mã lượt viếng thăm phát sinh đơn hàng.
- ngay_dat_hang (TEXT): Order date; used for time filtering and order-frequency/trend analysis.
- trang_thai (TEXT): Order status: NORMAL or CANCELLED.
- tong_tien (REAL): Order-header total amount (VND).

### Allowed joins
- don_hang_ban.cong_ty_id = cong_ty.cong_ty_id
- don_hang_ban.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id
- don_hang_ban.nhan_vien_id = nhan_vien.nhan_vien_id
- don_hang_ban.khach_hang_id = khach_hang.khach_hang_id
- don_hang_ban.vieng_tham_id = lich_su_vieng_tham.vieng_tham_id
- chi_tiet_don_hang_ban.don_hang_id = don_hang_ban.don_hang_id
- don_giao_hang.don_hang_id = don_hang_ban.don_hang_id
- hang_tra_ve.don_hang_id = don_hang_ban.don_hang_id

### Common values
- trang_thai: NORMAL, CANCELLED

### Retrieval text
don_hang_ban: Sales order header created by staff for a customer, usually from a visit. Aliases: sales order, don hang, don hang ban, order, revenue, sales amount, falling order frequency.

## Table: chi_tiet_don_hang_ban

### Business meaning
Chi tiết dòng hàng trong đơn bán gồm sản phẩm, số lượng, giá, khuyến mãi và thành tiền.
Sales order line: product, quantity, unit price, promotion, line total (net).

### Use this table when
- order line
- revenue
- doanh thu
- units sold
- product revenue
- thành tiền

### Primary key
chi_tiet_id

### Columns
- chi_tiet_id (INTEGER): Mã chi tiết đơn hàng.
- don_hang_id (TEXT): Mã đơn hàng bán.
- san_pham_id (TEXT): Mã sản phẩm.
- khuyen_mai_id (TEXT): Mã khuyến mãi, có thể để trống.
- so_luong (INTEGER): Quantity ordered on the line.
- don_gia (REAL): Unit selling price on the line (VND).
- thanh_tien (REAL): Line total after discount (VND) -- the basis of net revenue doanh_thu.

### Allowed joins
- chi_tiet_don_hang_ban.don_hang_id = don_hang_ban.don_hang_id
- chi_tiet_don_hang_ban.san_pham_id = san_pham.san_pham_id
- chi_tiet_don_hang_ban.khuyen_mai_id = khuyen_mai.khuyen_mai_id

### Retrieval text
chi_tiet_don_hang_ban: Sales order line: product, quantity, unit price, promotion, line total (net). Aliases: order item, sales line, chi tiet don hang, product revenue, units sold.

## Table: don_giao_hang

### Business meaning
Đơn giao hàng cho đơn bán, bao gồm ngày xuất kho và ngày giao.
Delivery record for an order, with warehouse-release and delivery dates.

### Use this table when
- delivery
- shipment
- giao hàng
- delivered/failed deliveries

### Primary key
giao_hang_id

### Columns
- giao_hang_id (INTEGER): Mã giao hàng.
- don_hang_id (TEXT): Mã đơn hàng bán.
- nha_phan_phoi_id (TEXT): Mã nhà phân phối giao hàng.
- ngay_xuat_kho (TEXT): Ngày xuất kho.
- ngay_giao (TEXT): Ngày giao hàng.
- trang_thai (TEXT): Trạng thái SHIPPED, DELIVERED hoặc FAILED.

### Allowed joins
- don_giao_hang.don_hang_id = don_hang_ban.don_hang_id
- don_giao_hang.nha_phan_phoi_id = nha_phan_phoi.nha_phan_phoi_id

### Common values
- trang_thai: DELIVERED, FAILED

### Retrieval text
don_giao_hang: Delivery record for an order, with warehouse-release and delivery dates. Aliases: delivery, shipment, don giao hang, delivered order.

## Table: hang_tra_ve

### Business meaning
Dòng hàng trả về gắn với đơn hàng bán gốc và sản phẩm.
Returned goods tied to the original order and product.

### Use this table when
- return
- refund
- hàng trả về
- returned quantity
- damaged goods

### Primary key
tra_ve_id

### Columns
- tra_ve_id (INTEGER): Mã hàng trả về.
- don_hang_id (TEXT): Mã đơn hàng bán gốc.
- san_pham_id (TEXT): Mã sản phẩm bị trả về.
- ngay_tra (TEXT): Ngày trả hàng.
- so_luong (INTEGER): Số lượng trả về.
- ly_do (TEXT): Lý do trả hàng.

### Allowed joins
- hang_tra_ve.don_hang_id = don_hang_ban.don_hang_id
- hang_tra_ve.san_pham_id = san_pham.san_pham_id

### Common values
- ly_do: giao_sai, can_date, hang_hong

### Retrieval text
hang_tra_ve: Returned goods tied to the original order and product. Aliases: return, refund, hang tra ve, returned product, damaged goods.
