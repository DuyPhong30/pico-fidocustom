# Hướng dẫn tuỳ chỉnh giao diện hình ảnh & GIF cho LILYGO T-Embed FIDO2

Bộ công cụ này giúp bạn chuyển đổi hình ảnh hoặc ảnh động (GIF) bất kỳ thành giao diện cho khóa bảo mật T-Embed.

## 1. Kích thước màn hình
Màn hình T-Embed ở chế độ dọc (portrait) có kích thước chuẩn: **170 × 320 pixels**.

Các trạng thái hiển thị của khóa:
1. **Idle (Chờ)**: Mèo ngủ / Trạng thái cắm vào máy tính sẵn sàng (`img_idle`).
2. **Waiting (Yêu cầu xác nhận)**: Mèo thức / Đang chờ người dùng bấm nút xoay (`img_waiting`).
3. **Confirmed (Thành công)**: Mèo nháy mắt thả tim / Đã xác thực thành công (`img_confirmed`).
4. **Timeout (Hết giờ)**: Mèo buồn / Hết thời gian 30s mà chưa bấm nút (`img_timeout`).

---

## 2. Cách tạo ảnh từ ảnh/GIF của bạn

### Bước 1: Cài thư viện Pillow (nếu chưa có)
```bash
pip install pillow
```

### Bước 2: Chuẩn bị 4 tấm ảnh hoặc file GIF
Đặt tên các file ví dụ:
- `idle.png` (hoặc `idle.gif`)
- `waiting.png` (hoặc `waiting.gif`)
- `confirmed.png` (hoặc `confirmed.gif`)
- `timeout.png` (hoặc `timeout.gif`)

### Bước 3: Chạy script chuyển đổi
```bash
python tools/image_converter.py idle.png waiting.png confirmed.png timeout.png
```
Script sẽ tự động:
- Cắt và co giãn (resize) ảnh về đúng chuẩn 170×320.
- Mã hoá sang chuẩn màu RGB565 và nén RLE siêu nhẹ.
- Xuất ra file `tembed_images.h` nằm ngay thư mục gốc repo.

### Bước 4: Commit và đẩy lên GitHub để nạp
Sau khi `tembed_images.h` được cập nhật:
```bash
git add tembed_images.h
git commit -m "Update custom UI images"
git push
```
GitHub Actions sẽ tự động compile ra file `pico_fido_tembed.bin` mới trong vòng 3 phút! Bạn chỉ cần tải về và nạp vào mạch.
