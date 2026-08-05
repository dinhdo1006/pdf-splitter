"""
pipeline/party_catalog.py
=========================
Danh mục 104 loại tài liệu số hóa hồ sơ đảng viên theo
Hướng dẫn số 1361-CV/BTCTW của Ban Tổ chức Trung ương — Phụ lục 1.

Cấu trúc ánh xạ:
    PARTY_DOC_CATALOG[doc_type_key] -> (STT_string, Ten_tai_lieu_khong_dau, Do_uu_tien)

    • doc_type_key  : Mã nhận diện nội bộ (UPPER_SNAKE_CASE)
    • STT_string    : Số thứ tự Phụ lục 1 dạng chuỗi raw ("1" … "104").
                      Dùng fmt_stt() để format đúng zero-padding ra đầu ra.
    • Ten_tai_lieu  : Tên tài liệu không dấu, giữ khoảng trắng (theo Phụ lục 2).
    • Do_uu_tien    : 1 (gốc & quyết định), 2 (thủ tục & sinh hoạt),
                      3 (văn bằng & công văn hành chính).

Priority defaults (edge case 5 — hardcoded theo dải STT):
    STT 01–36  -> Do_uu_tien = 1
    STT 37–85  -> Do_uu_tien = 2
    STT 86–104 -> Do_uu_tien = 3
"""

from __future__ import annotations

from loguru import logger


# ── Danh mục chính: {doc_type_key: (STT_raw_str, Ten_tai_lieu_khong_dau, Do_uu_tien)} ──
#
# STT_raw_str được lưu dạng số thập phân thuần (không có zero-padding).
# Zero-padding được áp dụng ở runtime bởi fmt_stt() theo edge case 4:
#   1-99   -> 2 chữ số   (01, 09, 10, 99)
#   100-104-> 3 chữ số   (100, 101, 104)
# ─────────────────────────────────────────────────────────────────────────────

PARTY_DOC_CATALOG: dict[str, tuple[str, str, int]] = {

    # ══ ĐỘ ƯU TIÊN 1: TÀI LIỆU GỐC & QUYẾT ĐỊNH QUAN TRỌNG (STT 01–36) ══

    "LY_LICH_NGUOI_XIN_VAO_DANG": (
        "1", "Ly lich cua nguoi xin vao Dang", 1,
    ),
    "LY_LICH_DANG_VIEN": (
        "2", "Ly lich dang vien", 1,
    ),
    "PHIEU_DANG_VIEN": (
        "3", "Phieu dang vien", 1,
    ),
    "PHIEU_BO_SUNG_HO_SO_DANG_VIEN": (
        "4", "Phieu bo sung ho so dang vien", 1,
    ),
    "QUYET_DINH_KET_NAP_DANG_VIEN": (
        "5", "Quyet dinh ket nap dang vien", 1,
    ),
    "QUYET_DINH_KET_NAP_LAI": (
        "6", "Quyet dinh ket nap lai nguoi vao Dang", 1,
    ),
    "QUYET_DINH_CONG_NHAN_DANG_VIEN_CHINH_THUC": (
        "7", "Quyet dinh cong nhan dang vien chinh thuc", 1,
    ),
    "QUYET_DINH_XOA_TEN_DANG_VIEN_DU_BI": (
        "8", "Quyet dinh cua cap uy co tham quyen xoa ten dang vien du bi", 1,
    ),
    "QUYET_DINH_CONG_NHAN_DANG_VIEN_SAU_KHAC_PHUC_KET_NAP": (
        "9", "Quyet dinh cong nhan dang vien sau khi khac phuc ket nap", 1,
    ),
    "QUYET_DINH_CONG_NHAN_DANG_VIEN_SAU_KHAC_PHUC_CHINH_THUC": (
        "10", "Quyet dinh cong nhan dang vien sau khi khac phuc chinh thuc", 1,
    ),
    "QUYET_DINH_HUY_KET_NAP_SAI_QUY_DINH": (
        "11", "Quyet dinh huy quyet dinh ket nap dang vien sai quy dinh", 1,
    ),
    "QUYET_DINH_HUY_KET_NAP_LAI_SAI_QUY_DINH": (
        "12", "Quyet dinh huy quyet dinh ket nap lai dang vien sai quy dinh", 1,
    ),
    "QUYET_DINH_HUY_CONG_NHAN_CHINH_THUC_SAI_QUY_DINH": (
        "13", "Quyet dinh huy quyet dinh cong nhan dang vien chinh thuc sai quy dinh", 1,
    ),
    "QUYET_DINH_KHOI_PHUC_QUYEN_DANG_VIEN": (
        "14", "Quyet dinh khoi phuc quyen cua dang vien", 1,
    ),
    "QUYET_DINH_NOI_LAI_SINH_HOAT_DANG": (
        "15", "Quyet dinh noi lai sinh hoat dang cua dang vien", 1,
    ),
    "QUYET_DINH_XOA_TEN_DANG_VIEN": (
        "16", "Quyet dinh xoa ten trong danh sach dang vien", 1,
    ),
    "QUYET_DINH_CHO_RA_KHOI_DANG": (
        "17", "Quyet dinh cho dang vien ra khoi Dang", 1,
    ),
    "GIAY_XAC_NHAN_TUOI_DANG": (
        "18", "Giay xac nhan tuoi Dang", 1,
    ),
    "QUYET_DINH_PHAT_THE_DANG_VIEN": (
        "19", "Quyet dinh phat the dang vien cho ca nhan dang vien", 1,
    ),
    "QUYET_DINH_TANG_HUY_HIEU_DANG": (
        "20", "Quyet dinh tang Huy hieu Dang doi voi ca nhan dang vien", 1,
    ),
    "QUYET_DINH_TRUY_TANG_HUY_HIEU_DANG": (
        "21", "Quyet dinh truy tang Huy hieu Dang doi voi ca nhan dang vien", 1,
    ),
    "QUYET_DINH_KY_LUAT_DANG": (
        "22", "Quyet dinh ky luat Dang", 1,
    ),
    "QUYET_DINH_KHEN_THUONG": (
        "23", "Quyet dinh khen thuong", 1,
    ),
    "QUYET_DINH_DINH_CHI_SINH_HOAT_DANG": (
        "24", "Quyet dinh dinh chi sinh hoat dang", 1,
    ),
    "QUYET_DINH_DINH_CHI_CAP_UY": (
        "25", "Quyet dinh dinh chi cap uy", 1,
    ),
    "QUYET_DINH_GIA_HAN_DINH_CHI_SINH_HOAT_DANG": (
        "26", "Quyet dinh gia han dinh chi sinh hoat dang", 1,
    ),
    "QUYET_DINH_GIA_HAN_DINH_CHI_CAP_UY": (
        "27", "Quyet dinh gia han dinh chi cap uy", 1,
    ),
    "QUYET_DINH_GIAI_QUYET_KHIEU_NAI": (
        "28", "Quyet dinh giai quyet khieu nai", 1,
    ),
    "QUYET_DINH_GIAI_QUYET_TO_CAO": (
        "29", "Quyet dinh giai quyet to cao", 1,
    ),
    "QUYET_DINH_DINH_CHI_CHUC_VU_TRONG_DANG": (
        "30", "Quyet dinh dinh chi chuc vu trong dang doi voi ca nhan", 1,
    ),
    "QUYET_DINH_CHO_TRO_LAI_SINH_HOAT_CAP_UY": (
        "31", "Quyet dinh cho tro lai sinh hoat cap uy", 1,
    ),
    "THONG_BAO_KET_LUAN_GIAI_QUYET_TO_CAO": (
        "32", "Thong bao ket luan giai quyet to cao", 1,
    ),
    "THONG_BAO_KHONG_GIAI_QUYET_KHIEU_NAI_KY_LUAT": (
        "33", "Thong bao ve viec khong giai quyet khieu nai ky luat dang", 1,
    ),
    "KET_LUAN_KIEM_TRA_KHI_CO_DAU_HIEU_VI_PHAM": (
        "34", "Ket luan kiem tra khi co dau hieu vi pham", 1,
    ),
    "KET_LUAN_MINH_OAN": (
        "35", "Ket luan minh oan khong vi pham cua co quan co tham quyen", 1,
    ),
    "QUYET_DINH_KY_LUAT_HANH_CHINH": (
        "36", "Quyet dinh ky luat hanh chinh", 1,
    ),

    # ══ ĐỘ ƯU TIÊN 2: HỒ SƠ THỦ TỤC, SINH HOẠT, KIỂM ĐIỂM HẰNG NĂM (STT 37–85) ══

    "DON_XIN_VAO_DANG": (
        "37", "Don xin vao Dang", 2,
    ),
    "GIAY_CHUNG_NHAN_LOP_NHAN_THUC_DANG": (
        "38", "Giay chung nhan hoc lop nhan thuc ve Dang", 2,
    ),
    "GIAY_GIOI_THIEU_NGUOI_VAO_DANG": (
        "39", "Giay gioi thieu nguoi vao Dang", 2,
    ),
    "NGHI_QUYET_GIOI_THIEU_DOAN_VIEN_UU_TU": (
        "40", "Nghi quyet gioi thieu doan vien uu tu vao Dang", 2,
    ),
    "NGHI_QUYET_GIOI_THIEU_DOAN_VIEN_CONG_DOAN": (
        "41", "Nghi quyet gioi thieu doan vien cong doan vao Dang", 2,
    ),
    "TONG_HOP_Y_KIEN_NHAN_XET_NGUOI_VAO_DANG": (
        "42", "Tong hop y kien nhan xet cua cac doan the voi nguoi vao Dang", 2,
    ),
    "NGHI_QUYET_DE_NGHI_KET_NAP_CUA_CHI_BO": (
        "43", "Nghi quyet de nghi ket nap dang vien cua chi bo", 2,
    ),
    "BAO_CAO_THAM_DINH_KET_NAP_DANG_UY_BO_PHAN": (
        "44", "Bao cao ve viec tham dinh nghi quyet cua chi bo de nghi ket nap", 2,
    ),
    "NGHI_QUYET_DE_NGHI_KET_NAP_DANG_UY_CO_SO": (
        "45", "Nghi quyet de nghi ket nap dang vien cua dang uy co so", 2,
    ),
    "GIAY_CHUNG_NHAN_LOP_DANG_VIEN_MOI": (
        "46", "Giay chung nhan hoc lop dang vien moi", 2,
    ),
    "BAN_TU_KIEM_DIEM_DANG_VIEN_DU_BI": (
        "47", "Ban tu kiem diem cua dang vien du bi", 2,
    ),
    "BAN_NHAN_XET_DANG_VIEN_DU_BI": (
        "48", "Ban nhan xet dang vien du bi cua dang vien giup do", 2,
    ),
    "TONG_HOP_Y_KIEN_NHAN_XET_DANG_VIEN_DU_BI": (
        "49", "Tong hop y kien nhan xet doi voi dang vien du bi", 2,
    ),
    "NGHI_QUYET_CONG_NHAN_CHINH_THUC_CHI_BO": (
        "50", "Nghi quyet de nghi cong nhan dang vien chinh thuc cua chi bo", 2,
    ),
    "BAO_CAO_THAM_DINH_CHINH_THUC_DANG_UY_BO_PHAN": (
        "51", "Bao cao tham dinh nghi quyet de nghi cong nhan dang vien chinh thuc", 2,
    ),
    "NGHI_QUYET_CONG_NHAN_CHINH_THUC_DANG_UY_CO_SO": (
        "52", "Nghi quyet de nghi cong nhan dang vien chinh thuc cua dang uy co so", 2,
    ),
    "GIAY_CHUNG_NHAN_DANG_XEM_XET_KET_NAP": (
        "53", "Giay chung nhan nguoi vao Dang dang trong thoi gian duoc giup do", 2,
    ),
    "GIAY_GIOI_THIEU_SINH_HOAT_DANG_CHINH_THUC": (
        "54", "Giay gioi thieu sinh hoat dang chinh thuc", 2,
    ),
    "GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI": (
        "55", "Giay gioi thieu sinh hoat dang tam thoi", 2,
    ),
    "GIAY_GIOI_THIEU_SINH_HOAT_DANG_NGOAI_NUOC": (
        "56", "Giay gioi thieu sinh hoat dang ra ngoai nuoc", 2,
    ),
    "PHIEU_CONG_TAC_CHINH_THUC_NGOAI_NUOC": (
        "57", "Phieu cong tac chinh thuc ngoai nuoc", 2,
    ),
    "PHIEU_CONG_TAC_TAM_THOI_NGOAI_NUOC": (
        "58", "Phieu cong tac tam thoi ngoai nuoc", 2,
    ),
    "GIAY_GIOI_THIEU_SINH_HOAT_DANG_NOI_BO": (
        "59", "Giay gioi thieu sinh hoat dang noi bo", 2,
    ),
    "PHIEU_BAO_CHUYEN_SINH_HOAT_DANG": (
        "60", "Phieu bao dang vien chuyen sinh hoat dang chinh thuc", 2,
    ),
    "BAN_TU_KIEM_DIEM_HANG_NAM": (
        "61", "Ban tu kiem diem hang nam va khi chuyen sinh hoat dang", 2,
    ),
    "BAN_TU_KIEM_DIEM_TAI_THOI_DIEM_CHUYEN": (
        "62", "Ban tu kiem diem tai thoi diem chuyen sinh hoat dang", 2,
    ),
    "BAN_KIEM_DIEM_SINH_HOAT_NGOAI_NUOC": (
        "63", "Ban kiem diem trong thoi gian sinh hoat dang o nuoc ngoai", 2,
    ),
    "QUYET_DINH_BIEU_DUONG_DANG_VIEN": (
        "64", "Quyet dinh bieu duong dang vien", 2,
    ),
    "BAN_TU_KIEM_DIEM_DANG_VIEN_VI_PHAM": (
        "65", "Ban tu kiem diem cua dang vien vi pham", 2,
    ),
    "THONG_BAO_VI_PHAM_PHAP_LUAT": (
        "66", "Thong bao vi pham cua co quan phap luat ban sao ban an hinh su", 2,
    ),
    "QUYET_DINH_GIAI_QUYET_KHIEU_NAI_KY_LUAT_HC": (
        "67", "Quyet dinh giai quyet khieu nai ve ky luat hanh chinh", 2,
    ),
    "BAN_AN_HINH_SU_CO_HIEU_LUC": (
        "68", "Ban an hinh su co hieu luc phap luat", 2,
    ),
    "KET_LUAN_THANH_TRA_KIEM_TOAN": (
        "69", "Ket luan thanh tra kiem toan lien quan den ca nhan dang vien", 2,
    ),
    "BANG_CHUNG_CHI_LY_LUAN_CHINH_TRI": (
        "70", "Bang chung chi ly luan chinh tri", 2,
    ),
    "QUYET_DINH_CAP_LAI_HUY_HIEU_DANG_BI_MAT": (
        "71", "Quyet dinh cap lai Huy hieu Dang bi mat", 2,
    ),
    "QUYET_DINH_CAP_LAI_THE_DANG_VIEN_BI_MAT": (
        "72", "Quyet dinh cap lai the dang vien bi mat", 2,
    ),
    "QUYET_DINH_DOI_THE_DANG_VIEN_BI_HONG": (
        "73", "Quyet dinh doi lai the dang vien bi hong bi sai", 2,
    ),
    "QUYET_DINH_THAY_DOI_HO_TEN": (
        "74", "Quyet dinh cua Toa an Trich luc ho tich ve thay doi ho ten", 2,
    ),
    "KHAI_SINH_GOC_DINH_CHINH_NGAY_SINH": (
        "75", "Khai sinh goc Ket luan dinh chinh ngay thang nam sinh", 2,
    ),
    "XAC_NHAN_THAY_DOI_QUE_QUAN_NOI_CU_TRU": (
        "76", "Xac nhan ve thay doi que quan noi cu tru", 2,
    ),
    "QUYET_DINH_XAC_DINH_LAI_DAN_TOC": (
        "77", "Quyet dinh xac dinh lai dan toc cua UBND cap tinh", 2,
    ),
    "KET_LUAN_HUY_VAN_BANG": (
        "78", "Ket luan huy van bang xac dinh van bang khong hop le", 2,
    ),
    "DON_XIN_MIEN_SINH_HOAT_DANG": (
        "79", "Don xin mien cong tac va sinh hoat dang", 2,
    ),
    "GIAY_XAC_NHAN_Y_TE_MIEN_SINH_HOAT": (
        "80", "Giay xac nhan cua co so y te khi mien vi ly do suc khoe", 2,
    ),
    "NGHI_QUYET_CHO_MIEN_SINH_HOAT_DANG": (
        "81", "Nghi quyet chi bo ve viec cho dang vien mien cong tac va sinh hoat", 2,
    ),
    "VAN_BAN_BO_SUNG_LY_LICH_SAU_VE_NUOC": (
        "82", "Van ban bo sung ly lich sau khi ve nuoc", 2,
    ),
    "GIAY_CHUNG_TU_CUA_DANG_VIEN": (
        "83", "Giay chung tu cua dang vien", 2,
    ),
    "BAN_TUONG_TRINH_MAT_HO_SO_DANG_VIEN": (
        "84", "Ban tuong trinh ve viec mat ho so dang vien kem ban kiem diem", 2,
    ),
    "PHIEU_DANG_VIEN_CU_LUU_LICH_SU": (
        "85", "Phieu dang vien cu cac phien ban truoc khong huy luu lam lich su", 2,
    ),

    # ══ ĐỘ ƯU TIÊN 3: VĂN BẰNG CHUYÊN MÔN, NHÂN SỰ & CÔNG VĂN (STT 86–104) ══

    "CAC_VAN_BANG_CHUNG_CHI_CHUYEN_MON": (
        "86", "Cac van bang chung chi chuyen mon nghiep vu ngoai ngu tin hoc", 3,
    ),
    "CAC_QUYET_DINH_DIEU_DONG_BO_NHIEM": (
        "87", "Cac quyet dinh dieu dong bo nhiem", 3,
    ),
    "QUYET_DINH_NGHI_HUU": (
        "88", "Quyet dinh nghi huu", 3,
    ),
    "QUYET_DINH_NGHI_MAT_SUC": (
        "89", "Quyet dinh nghi mat suc lao dong", 3,
    ),
    "QUYET_DINH_PHUC_VIEN_CHUYEN_NGANH": (
        "90", "Quyet dinh phuc vien chuyen nganh", 3,
    ),
    "QUYET_DINH_XUAT_NGU": (
        "91", "Quyet dinh xuat ngu", 3,
    ),
    "CONG_VAN_GIOI_THIEU_DE_NGHI_KET_NAP_CHI_BO": (
        "92", "Cong van gioi thieu nguoi vao Dang da duoc chi bo de nghi ket nap", 3,
    ),
    "CONG_VAN_GIOI_THIEU_DE_NGHI_KET_NAP_CAP_UY": (
        "93", "Cong van gioi thieu nguoi vao Dang da duoc cap uy de nghi ket nap", 3,
    ),
    "CONG_VAN_GIOI_THIEU_DA_RA_QUYET_DINH_KET_NAP": (
        "94", "Cong van gioi thieu nguoi vao Dang da duoc ra quyet dinh ket nap", 3,
    ),
    "CONG_VAN_GIOI_THIEU_KET_NAP_CHUYEN_NGOAI_XA": (
        "95", "Cong van gioi thieu nguoi vao Dang chuyen ra ngoai dang bo cap xa", 3,
    ),
    "GIAY_GIOI_THIEU_CU_DANG_VIEN_THAM_TRA": (
        "96", "Giay gioi thieu cu dang vien hoac cap uy vien di tham tra ly lich", 3,
    ),
    "CONG_VAN_DE_NGHI_THAM_TRA_LY_LICH": (
        "97", "Cong van de nghi tham tra ly lich cua nguoi vao Dang", 3,
    ),
    "CONG_VAN_CHI_DAO_THU_TUC_KET_NAP": (
        "98", "Cong van chi dao viec lam lai thuc hien dung thu tuc ket nap", 3,
    ),
    "CONG_VAN_CHI_DAO_THU_TUC_CHINH_THUC": (
        "99", "Cong van chi dao viec lam lai thuc hien dung thu tuc chinh thuc", 3,
    ),
    "PHIEU_BAO_CONG_NHAN_CHINH_THUC": (
        "100", "Phieu bao dang vien duoc cong nhan chinh thuc", 3,
    ),
    "PHIEU_BAO_RA_KHOI_DANG": (
        "101", "Phieu bao dang vien ra khoi Dang", 3,
    ),
    "PHIEU_BAO_TU_TRAN": (
        "102", "Phieu bao dang vien tu tran", 3,
    ),
    "TO_KHAI_DE_NGHI_TANG_HUY_HIEU_DANG": (
        "103", "To khai de nghi tang Huy hieu Dang", 3,
    ),
    "TO_KHAI_DE_NGHI_TRUY_TANG_HUY_HIEU_DANG": (
        "104", "To khai de nghi truy tang Huy hieu Dang", 3,
    ),
}


# ── Bảng tra ngược: STT_int -> doc_type_key (xây dựng 1 lần khi import) ─────
_STT_INT_TO_KEY: dict[int, str] = {
    int(v[0]): k for k, v in PARTY_DOC_CATALOG.items()
}


# ── Hàm tiện ích ─────────────────────────────────────────────────────────────

def fmt_stt(stt: str | int) -> str:
    """
    Áp dụng zero-padding chuẩn cho STT (edge case 4):
      1–99   -> 2 chữ số   ("01", "09", "10", "99")
      100–104-> 3 chữ số   ("100", "104")

    Nhận cả chuỗi lẫn số nguyên.

    Raises:
        ValueError: Nếu STT nằm ngoài dải 1–104.
    """
    n = int(stt)
    if n < 1 or n > 104:
        raise ValueError(
            f"STT phải nằm trong khoảng 1–104, nhận được: {stt!r}"
        )
    return f"{n:03d}" if n >= 100 else f"{n:02d}"


def lookup_by_key(doc_type_key: str) -> tuple[str, str, int] | None:
    """
    Tra cứu catalog theo mã nội bộ (case-insensitive).

    Trả về:
        (stt_formatted, ten_tai_lieu_khong_dau, do_uu_tien)  hoặc  None.

    STT đã được format chuẩn qua fmt_stt().
    """
    entry = PARTY_DOC_CATALOG.get(doc_type_key.upper())
    if entry is None:
        logger.warning(
            "[catalog] Không tìm thấy doc_type_key={!r}", doc_type_key
        )
        return None
    stt_raw, ten, uu_tien = entry
    return fmt_stt(stt_raw), ten, uu_tien


def lookup_by_stt(stt: str | int) -> tuple[str, str, str, int] | None:
    """
    Tra cứu catalog theo STT (int hoặc str).

    Trả về:
        (doc_type_key, stt_formatted, ten_tai_lieu_khong_dau, do_uu_tien)
        hoặc None nếu không tìm thấy.
    """
    try:
        n = int(stt)
        stt_fmt = fmt_stt(n)
    except (ValueError, TypeError):
        logger.warning("[catalog] STT không hợp lệ: {!r}", stt)
        return None

    key = _STT_INT_TO_KEY.get(n)
    if key is None:
        logger.warning("[catalog] Không có STT {} trong catalog", stt_fmt)
        return None
    _, ten, uu_tien = PARTY_DOC_CATALOG[key]
    return key, stt_fmt, ten, uu_tien


def priority_from_stt(stt: str | int) -> int:
    """
    Edge case 5 — trả về độ ưu tiên mặc định theo dải STT,
    bổ sung cho những ô 'Độ ưu tiên' bị trống trong Phụ lục 1.

      STT 01–36  -> 1  (kể cả STT 5, 6, 16, 17, 20, 21, 22 thường bị bỏ trống)
      STT 37–85  -> 2
      STT 86–104 -> 3

    Raises:
        ValueError: Nếu STT nằm ngoài dải 1–104.
    """
    n = int(stt)
    if 1 <= n <= 36:
        return 1
    if 37 <= n <= 85:
        return 2
    if 86 <= n <= 104:
        return 3
    raise ValueError(f"STT {n} nằm ngoài dải 1–104")


def all_keys_sorted_by_priority() -> list[str]:
    """
    Trả về danh sách tất cả doc_type_key sắp xếp theo:
      1. Do_uu_tien tăng dần (1 -> 3)
      2. STT tăng dần trong cùng mức ưu tiên
    """
    return sorted(
        PARTY_DOC_CATALOG.keys(),
        key=lambda k: (PARTY_DOC_CATALOG[k][2], int(PARTY_DOC_CATALOG[k][0])),
    )


# ── Tự kiểm tra tính nhất quán khi module được import ───────────────────────

def _validate_catalog() -> None:
    """
    Kiểm tra catalog:
      - Đúng 104 mục
      - Không trùng STT
      - Dải STT liên tục 1–104
      - Mọi Do_uu_tien khớp với priority_from_stt()
    """
    total = len(PARTY_DOC_CATALOG)
    errors: list[str] = []

    if total != 104:
        errors.append(
            f"Catalog phải có đúng 104 mục — hiện có {total}"
        )

    seen_stts: dict[int, str] = {}
    for key, (stt_raw, ten, uu_tien) in PARTY_DOC_CATALOG.items():
        n = int(stt_raw)

        # Trùng STT
        if n in seen_stts:
            errors.append(
                f"STT trùng lặp {n}: {seen_stts[n]!r} vs {key!r}"
            )
        seen_stts[n] = key

        # Tên tài liệu không được rỗng
        if not ten.strip():
            errors.append(f"Tên tài liệu rỗng tại key={key!r}")

        # Kiểm tra Do_uu_tien khớp với dải hardcode (edge case 5)
        expected_uu = priority_from_stt(n)
        if uu_tien != expected_uu:
            errors.append(
                f"Do_uu_tien không khớp tại STT {n:>3d} ({key!r}): "
                f"catalog={uu_tien}, expected={expected_uu}"
            )

    # Dải liên tục
    expected_stts = set(range(1, 105))
    actual_stts = set(seen_stts.keys())
    missing = expected_stts - actual_stts
    extra = actual_stts - expected_stts
    if missing:
        errors.append(f"Thiếu STT: {sorted(missing)}")
    if extra:
        errors.append(f"STT thừa/không hợp lệ: {sorted(extra)}")

    if errors:
        for err in errors:
            logger.error("[catalog] {}", err)
    else:
        logger.debug(
            "[catalog] Catalog hợp lệ: {} mục, STT 01–104 đầy đủ, "
            "priority nhất quán.",
            total,
        )


_validate_catalog()


# ── Smoke-test CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG", colorize=True)

    print(f"\n{'=' * 68}")
    print(f"  PARTY_DOC_CATALOG — {len(PARTY_DOC_CATALOG)} mục")
    print(f"{'=' * 68}")
    for key in all_keys_sorted_by_priority():
        stt_raw, ten, uu_tien = PARTY_DOC_CATALOG[key]
        stt = fmt_stt(stt_raw)
        print(f"  [{stt}] P{uu_tien}  {ten[:55]:<55}  <- {key}")

    print(f"\n--- lookup_by_key ---")
    print(lookup_by_key("GIAY_GIOI_THIEU_SINH_HOAT_DANG_TAM_THOI"))
    print(lookup_by_key("KHONG_TON_TAI"))

    print(f"\n--- lookup_by_stt ---")
    for s in [1, 55, 100, 104, 105]:
        print(f"  stt={s!r:>5} -> {lookup_by_stt(s)}")

    print(f"\n--- priority_from_stt (edge case 5) ---")
    for n in [1, 5, 16, 17, 20, 21, 22, 36, 37, 85, 86, 104]:
        print(f"  stt={n:>3d} -> priority {priority_from_stt(n)}")
