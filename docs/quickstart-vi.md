# Hướng dẫn thử Web Growth Guard

Bản thử nghiệm công khai [v0.6.0rc1](https://github.com/autominhlinh/web-growth-guard/releases/tag/v0.6.0rc1) dùng giấy phép MIT. Công cụ kiểm tra lỗi SEO phát sinh khi cập nhật web, không yêu cầu tài khoản hay API key. Python 3.11+ và filesystem POSIX; đã kiểm thử Linux, chưa xác minh macOS, Windows native hoặc WSL. Trên Windows có thể thử trong môi trường Linux/WSL với filesystem Linux.

## Chạy demo trong môi trường riêng

```sh
git clone --branch v0.6.0rc1 --single-branch https://github.com/autominhlinh/web-growth-guard.git
cd web-growth-guard
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/demo_regression.py
```

Kết quả mong đợi: baseline không có lỗi trong phạm vi kiểm tra, candidate trả `BLOCK` do sai canonical và `noindex`. Demo chỉ thay đổi file giả lập tạm thời, không truy cập website thật. Cài từ mã nguồn có thể cần tải công cụ build; phần runtime không cần dependency ngoài.

## Dashboard và MCP

```sh
mkdir demo-output
python examples/demo_growth.py --output demo-output/marketing
python examples/demo_intelligence.py --output demo-output/intelligence
python examples/demo_analytics.py --output demo-output/analytics
```

Mở các file `dashboard.html` tương ứng. Dữ liệu CSV và HTML đều giả lập, không phải báo cáo traffic thực tế. Hướng dẫn thiết lập snapshot và chạy SDK: [Quickstart](quickstart.md), [README](../README.md). MCP chỉ đọc phạm vi đã cấu hình, không đăng quảng cáo, ghi file, chạy lệnh hoặc triển khai. Kết quả `CLEAR` không phải lệnh deploy và không bảo đảm lên top.

## Phản hồi

Gửi nhận xét hoặc lỗi tái hiện bằng dữ liệu giả lập tại [issue phản hồi](https://github.com/autominhlinh/web-growth-guard/issues/1). Không đăng file cấu hình, dữ liệu khách hàng, token, báo cáo website thật hoặc số liệu analytics cá nhân. Báo cáo lỗ hổng qua kênh riêng tư theo [SECURITY.md](../SECURITY.md).
