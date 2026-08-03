# K4 E-commerce Crawler

## Mục đích

`crawl_k4_ecommerce.py` là crawler nhỏ dùng để lấy các trang chính sách thương mại điện tử công khai và chuyển chúng thành các file Markdown phù hợp với corpus K4.

Script chỉ crawl các URL được chỉ định trong file CSV. Script không tự động dò hoặc crawl toàn bộ website.

## Cấu trúc thư mục đầu ra

```text
data/k4_ecommerce/
├── <doc-id>.md
└── sources.csv
```

Mỗi file Markdown gồm YAML front matter và nội dung đã được trích xuất, làm sạch từ trang HTML.

## File đầu vào

Có thể dùng file đầy đủ [k4_urls.csv](k4_urls.csv). File mẫu tối thiểu [k4_urls.example.csv](k4_urls.example.csv) chỉ dùng để tham khảo cấu trúc.

Cột bắt buộc:

```csv
url
```

Các cột metadata được hỗ trợ:

```csv
url,doc_id,title,customer_role,category,language,document_version,license_or_permission
```

Trong đó:

- `url`: URL gốc của trang công khai.
- `doc_id`: mã tài liệu duy nhất; nếu bỏ trống, script tự tạo từ tiêu đề.
- `title`: tiêu đề tài liệu; nếu bỏ trống, script lấy từ thẻ HTML `<title>`.
- `customer_role`: bắt buộc là `buyer`, `seller` hoặc `both`.
- `category`: nhóm chính sách, ví dụ `returns`, `shipping`, `payment`, `terms`.
- `language`: ngôn ngữ tài liệu, thường là `vi`.
- `document_version`: phiên bản hoặc ngày hiệu lực; dùng `not-stated` nếu nguồn không nêu.
- `license_or_permission`: căn cứ sử dụng, ví dụ `public-page`.

## Cách chạy

Từ thư mục gốc repository:

```bash
python scripts/crawl_k4_ecommerce.py \
  scripts/k4_urls.csv \
  --output-dir data/k4_ecommerce
```

Trên PowerShell:

```powershell
python scripts/crawl_k4_ecommerce.py `
  scripts/k4_urls.csv `
  --output-dir data/k4_ecommerce
```

Một số tùy chọn:

```text
--output-dir DIR       Thư mục chứa file .md và sources.csv
--delay SECONDS        Khoảng chờ giữa các request, tối thiểu 1 giây
--timeout SECONDS      Timeout cho mỗi request
--user-agent VALUE     User-Agent gửi tới website
--overwrite            Ghi đè file .md đã tồn tại
```

Ví dụ ghi đè tài liệu đã crawl:

```bash
python scripts/crawl_k4_ecommerce.py \
  scripts/k4_urls.csv \
  --output-dir data/k4_ecommerce \
  --overwrite
```

## Kiểm tra trước khi crawl

1. Xác nhận URL thuộc đúng phạm vi chính sách K4.
2. Kiểm tra điều khoản sử dụng và `robots.txt` của website.
3. Chỉ sử dụng nội dung công khai và được phép sử dụng.
4. Không dùng crawler cho nội dung cần đăng nhập, CAPTCHA, API riêng tư, PDF hoặc trang JavaScript không có nội dung HTML khả dụng.
5. Đọc lại file Markdown sau khi crawl để loại bỏ phần thừa và kiểm tra các điều kiện, ngoại lệ, thời hạn quan trọng.
6. Kiểm tra `sources.csv` có khớp một-một với các tài liệu trong thư mục hay không.

## Kết quả đầu ra

Ví dụ front matter:

```yaml
---
doc_id: "k4-shopee-terms"
title: "Điều khoản Shopee"
customer_role: "both"
category: "terms"
language: "vi"
source_url: "https://help.shopee.vn/..."
retrieved_at: "2026-08-03"
document_version: "not-stated"
---
```

`source_url` và `retrieved_at` giúp truy vết nguồn. `customer_role` và các metadata khác được giữ lại khi `ingest.py` nạp tài liệu, từ đó có thể dùng cho `search_with_filter()`.

## Xử lý lỗi

- URL bị chặn bởi `robots.txt`: tài liệu bị bỏ qua và lỗi được ghi ra màn hình.
- Nội dung không phải HTML/text: tài liệu bị bỏ qua.
- Nội dung quá ngắn: tài liệu bị bỏ qua để tránh tạo corpus rỗng hoặc không hữu ích.
- File đã tồn tại: dùng `--overwrite` nếu thực sự muốn thay thế.

Crawler không xóa file cũ. `sources.csv` được cập nhật theo `doc_id`, nên các tài liệu hiện có vẫn được giữ lại.
