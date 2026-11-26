# Party App PGI 2026

Trò chơi tập thể trên điện thoại cho tiệc 100–200 người: Đố vui tính điểm theo nhóm, Rung chuông vàng, Bình chọn, Quay số trúng thưởng.

| Trang | Ai dùng |
|---|---|
| `/` | Người chơi (quét QR trên màn hình lớn) |
| `/screen` | Máy chiếu / màn LED |
| `/mc?key=MC_KEY` | MC điều khiển (lần đầu mở bằng link có mã, sau đó nhớ 7 ngày) |

## Deploy trên Render
- New → Web Service → chọn repo. **Language: Python 3**
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`
- Environment:
  - `MC_KEY` = mã bí mật của MC (bắt buộc)
  - `PYTHON_VERSION` = `3.12.7`
  - `DATABASE_URL` = Internal Database URL của Postgres (không bắt buộc, có thì khởi động lại không mất điểm)
  - `EVENT_NAME` = tên chương trình (mặc định "Party App PGI 2026")

Luôn chạy **1 worker**: trạng thái trò chơi nằm trong bộ nhớ của một tiến trình.

## Âm thanh
Nhạc nền và hiệu ứng được tổng hợp ngay trên trình duyệt của màn hình lớn (giai điệu tự soạn, không dùng file nhạc).
Mở `/screen`, **bấm một lần vào trang** (nút "Bấm để bật âm thanh" hoặc "Toàn màn hình") để trình duyệt cho phát tiếng.
MC bật/tắt và chỉnh âm lượng ở tab **Sảnh chờ**. Điện thoại người chơi không phát âm thanh.

## Soạn câu hỏi
Đố vui và Rung chuông vàng có **bộ câu hỏi riêng**. Google Sheet theo cột: `Câu hỏi | A | B | C | D | Đáp án (A/B/C/D) | Số giây`.
Bôi đen, Ctrl+C, dán vào ô của trò tương ứng ở tab **Câu hỏi** trên trang MC. Khi bắt đầu trò, câu hỏi được lấy ngẫu nhiên; MC chọn số câu sẽ chơi.

## Kiểm thử
```
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
python -m pytest -q                       # logic trò chơi
python tests/e2e_browser.py               # trình duyệt thật: iPhone + màn hình lớn + MC
python tests/load_test.py --n 200         # 200 điện thoại giả lập trên máy
python tests/load_test.py --url https://<app>.onrender.com --key <MC_KEY> --n 150   # thử trên máy chủ thật (xoá hết người chơi!)
```
