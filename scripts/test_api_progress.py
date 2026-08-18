"""
Script test và kiểm chứng API tiến độ thời gian thực (Real-time Progress Test)

Cách chạy:
    python scripts/test_api_progress.py <JOB_ID>
    python scripts/test_api_progress.py <JOB_ID> --url http://10.10.4.21:8090
    python scripts/test_api_progress.py <JOB_ID> --stream
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test API tiến độ bóc tách PDF realtime")
    parser.add_argument("job_id", help="Tên Job ID cần theo dõi tiến độ")
    parser.add_argument(
        "--url",
        default="http://localhost:8090",
        help="Base URL của API (mặc định: http://localhost:8090)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Chu kỳ poll (giây, mặc định: 1.0s)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Dùng kết nối SSE stream (/stream) thay vì Polling",
    )
    return parser.parse_args()


def render_bar(percent: float, length: int = 30) -> str:
    filled = int(round(length * (percent / 100.0)))
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {percent:5.1f}%"


def test_polling(base_url: str, job_id: str, interval: float) -> int:
    api_url = f"{base_url.rstrip('/')}/api/jobs/{job_id}"
    print(f"\n🚀 Đang theo dõi tiến độ qua REST API Polling: {api_url}")
    print("=" * 75)

    last_page = -1
    last_pct = -1.0

    while True:
        try:
            req = Request(api_url, headers={"User-Agent": "TestScript/1.0"})
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                print(f"⏳ Job '{job_id}' chưa bắt đầu hoặc chưa thấy trong hệ thống (404)...")
                time.sleep(interval)
                continue
            print(f"\n❌ Lỗi HTTP {e.code}: {e.read().decode('utf-8')}")
            return 1
        except URLError as e:
            print(f"\n❌ Không kết nối được tới API tại {base_url}: {e.reason}")
            return 1

        status = data.get("status", "unknown")
        pct = float(data.get("percent", 0.0))
        cur_p = int(data.get("current_page", 0))
        tot_p = int(data.get("total_pages", 0))
        stage = str(data.get("stage", "Đang xử lý"))
        elapsed = data.get("elapsed_seconds", 0.0)
        eta = data.get("eta_seconds", 0.0)

        # In ra màn hình tiến độ thời gian thực
        bar = render_bar(pct)
        eta_str = f"ETA: {eta:.1f}s" if eta else "ETA: --"
        elapsed_str = f"Thời gian: {elapsed:.1f}s" if elapsed else ""
        page_str = f"Trang {cur_p}/{tot_p}" if tot_p > 0 else "Đang khởi tạo"

        sys.stdout.write(
            f"\r{bar} | {page_str:<15} | {elapsed_str:<16} | {eta_str:<11} | {stage:<30}"
        )
        sys.stdout.flush()

        if status == "completed":
            print(f"\n\n✅ [100%] BÓC TÁCH HOÀN TẤT THÀNH CÔNG!")
            print(f"📁 Thư mục kết quả MinIO: {data.get('output_prefix')}")
            stats = data.get("stats", {})
            if stats:
                print(
                    f"📊 Thống kê: Thành công {stats.get('success_count', 0)} file | "
                    f"Mồ côi {stats.get('orphan_count', 0)} trang"
                )
            return 0
        elif status == "failed":
            print(f"\n\n❌ [FAILED] JOB BỊ LỖI!")
            print(f"Chi tiết lỗi: {data.get('error')}")
            return 1

        time.sleep(interval)


def test_sse_stream(base_url: str, job_id: str) -> int:
    stream_url = f"{base_url.rstrip('/')}/api/jobs/{job_id}/stream"
    print(f"\n🚀 Đang mở kết nối SSE Stream: {stream_url}")
    print("=" * 75)

    try:
        req = Request(
            stream_url,
            headers={"User-Agent": "TestScript/1.0", "Accept": "text/event-stream"},
        )
        with urlopen(req, timeout=300) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if not line_str or not line_str.startswith("data:"):
                    continue
                json_str = line_str[5:].strip()
                data = json.loads(json_str)

                status = data.get("status", "unknown")
                pct = float(data.get("percent", 0.0))
                cur_p = int(data.get("current_page", 0))
                tot_p = int(data.get("total_pages", 0))
                stage = str(data.get("stage", "Đang xử lý"))
                elapsed = data.get("elapsed_seconds", 0.0)
                eta = data.get("eta_seconds", 0.0)

                bar = render_bar(pct)
                eta_str = f"ETA: {eta:.1f}s" if eta else "ETA: --"
                elapsed_str = f"Thời gian: {elapsed:.1f}s" if elapsed else ""
                page_str = f"Trang {cur_p}/{tot_p}" if tot_p > 0 else "Đang khởi tạo"

                sys.stdout.write(
                    f"\r{bar} | {page_str:<15} | {elapsed_str:<16} | {eta_str:<11} | {stage:<30}"
                )
                sys.stdout.flush()

                if status == "completed":
                    print(f"\n\n✅ [100%] BÓC TÁCH HOÀN TẤT THÀNH CÔNG (SSE STREAM)!")
                    return 0
                elif status == "failed":
                    print(f"\n\n❌ [FAILED] JOB BỊ LỖI (SSE STREAM): {data.get('error')}")
                    return 1
    except Exception as e:
        print(f"\n❌ Lỗi kết nối Stream: {e}")
        return 1
    return 0


def main() -> int:
    args = parse_args()
    if args.stream:
        return test_sse_stream(args.url, args.job_id)
    return test_polling(args.url, args.job_id, args.interval)


if __name__ == "__main__":
    sys.exit(main())
