#!/usr/bin/env python3
"""Parse WHS care-guide Excel into bilingual JSON consumed by the API.

Usage:
  python3 scripts/build_whs_guide.py

Outputs
-------
  data/hospitals.json
  data/guides/orpha-280.json
  data/hospital_logo_map.json

English fields (name_en / bio_en / …) are preserved from any existing JSON.
Run scripts/translate_whs_guide_en.py after a fresh Excel import to fill EN.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path

try:
    import openpyxl
except ImportError as exc:
    raise SystemExit("openpyxl required: pip install openpyxl") from exc

ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "data" / "sources" / "WHS就医手册内容表(1).xlsx"
OUT_DIR = ROOT / "data"
GUIDES_DIR = OUT_DIR / "guides"

CITY_COORDS = {
    "北京市": (39.9042, 116.4074),
    "天津市": (39.3434, 117.3616),
    "上海市": (31.2304, 121.4737),
    "重庆市": (29.4316, 106.9123),
    "辽宁省": (41.8057, 123.4315),
    "黑龙江省": (45.8038, 126.5340),
    "江苏省": (32.0603, 118.7969),
    "浙江省": (30.2741, 120.1551),
    "江西省": (28.6820, 115.8579),
    "广东省": (23.1291, 113.2644),
    "广西壮族自治区": (22.8170, 108.3669),
    "四川省": (30.5728, 104.0668),
    "陕西省": (34.3416, 108.9398),
    "湖北省": (30.5928, 114.3055),
    "河南省": (34.7466, 113.6254),
    "湖南省": (28.2282, 112.9388),
}

CITY_EN = {
    "北京市": "Beijing",
    "天津市": "Tianjin",
    "上海市": "Shanghai",
    "重庆市": "Chongqing",
    "辽宁省": "Liaoning",
    "黑龙江省": "Heilongjiang",
    "江苏省": "Jiangsu",
    "浙江省": "Zhejiang",
    "江西省": "Jiangxi",
    "广东省": "Guangdong",
    "广西壮族自治区": "Guangxi",
    "四川省": "Sichuan",
    "陕西省": "Shaanxi",
    "湖北省": "Hubei",
    "河南省": "Henan",
    "湖南省": "Hunan",
}

SUMMARY_ZH = (
    "一种由 4 号染色体短臂缺失引起的罕见遗传病，常伴生长发育迟缓、"
    "特殊面容、癫痫与多系统受累。本页提供面向患者的就医导览与相关专家信息，"
    "不构成诊疗建议。"
)
SUMMARY_EN = (
    "A rare genetic condition caused by a deletion on the short arm of "
    "chromosome 4, often featuring growth delay, characteristic facial "
    "features, epilepsy, and multi-system involvement. This page offers "
    "patient-oriented care navigation and specialist information — not "
    "medical advice."
)
CARE_TIPS_ZH = [
    "优先考虑设有罕见病门诊 / 遗传代谢 / 发育儿科 / 儿童神经 / 康复的医疗机构。",
    "可携带已有的基因检测报告、生长发育记录与癫痫相关检查，便于多学科评估。",
    "就近排序按省市估算，实际就诊请以医院官方预约渠道为准。",
]
CARE_TIPS_EN = [
    "Prioritise centres with rare-disease clinics, genetics / metabolic medicine, developmental paediatrics, paediatric neurology, or rehabilitation.",
    "Bring existing genetic reports, growth records, and epilepsy-related tests to support multidisciplinary assessment.",
    "Nearest ranking is estimated by province/city centre — always confirm booking via official hospital channels.",
]
KEYWORDS_ZH = ["罕见病", "遗传代谢", "发育儿科", "儿童神经", "康复"]
KEYWORDS_EN = [
    "Rare disease",
    "Genetics / metabolism",
    "Developmental paediatrics",
    "Paediatric neurology",
    "Rehabilitation",
]


def slug_hospital(name: str) -> str:
    return "h_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


def expert_type(name: str) -> str:
    if any(k in name for k in ("团队", "中心", "MDT")):
        return "team"
    if any(k in name for k in ("科", "门诊")) and "医师" not in name and "医生" not in name:
        return "department"
    return "doctor"


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_existing_logo_map(path: Path) -> dict:
    data = load_json(path)
    return dict(data.get("logos") or {})


def index_existing_hospitals(path: Path) -> dict:
    """id / name_zh → previous hospital record (to keep EN + logo)."""
    data = load_json(path)
    out = {}
    for h in data.get("hospitals") or []:
        if h.get("id"):
            out[h["id"]] = h
        key = h.get("name_zh") or h.get("name")
        if key:
            out[key] = h
    return out


def index_existing_experts(path: Path) -> dict:
    data = load_json(path)
    return {e["id"]: e for e in (data.get("experts") or []) if e.get("id")}


def main() -> None:
    if not XLSX.is_file():
        raise SystemExit(f"Excel not found: {XLSX}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)

    existing_logos = load_existing_logo_map(OUT_DIR / "hospital_logo_map.json")
    prev_hosp = index_existing_hospitals(OUT_DIR / "hospitals.json")
    prev_guide = load_json(GUIDES_DIR / "orpha-280.json")
    prev_experts = index_existing_experts(GUIDES_DIR / "orpha-280.json")

    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.active
    hospitals: OrderedDict[str, dict] = OrderedDict()
    experts: list[dict] = []

    for row in list(ws.iter_rows(values_only=True))[1:]:
        if not row or not row[0]:
            continue
        hosp_name = str(row[0]).strip()
        city = str(row[2] or "").strip()
        advantage = str(row[3] or "").strip()
        expert_name = str(row[4] or "").strip()
        bio = str(row[5] or "").strip()

        if hosp_name not in hospitals:
            hid = slug_hospital(hosp_name)
            prev = prev_hosp.get(hid) or prev_hosp.get(hosp_name) or {}
            lat, lng = CITY_COORDS.get(city, (None, None))
            hospitals[hosp_name] = {
                "id": hid,
                "name_zh": hosp_name,
                "name_en": prev.get("name_en") or "",
                "city_zh": city,
                "city_en": prev.get("city_en") or CITY_EN.get(city) or city,
                "lat": lat,
                "lng": lng,
                "logo": existing_logos.get(hosp_name) or prev.get("logo"),
                "advantage_zh": advantage,
                "advantage_en": prev.get("advantage_en") or "",
            }
        elif advantage and not hospitals[hosp_name]["advantage_zh"]:
            hospitals[hosp_name]["advantage_zh"] = advantage

        eid = "e_" + hashlib.md5(f"{hosp_name}|{expert_name}".encode()).hexdigest()[:12]
        prev_e = prev_experts.get(eid) or {}
        experts.append(
            {
                "id": eid,
                "name_zh": expert_name,
                "name_en": prev_e.get("name_en") or "",
                "type": expert_type(expert_name),
                "hospital_id": hospitals[hosp_name]["id"],
                "bio_zh": bio,
                "bio_en": prev_e.get("bio_en") or "",
                "disease_ids": ["ORPHA:280"],
            }
        )

    hosp_list = list(hospitals.values())
    for h in hosp_list:
        h["logo"] = existing_logos.get(h["name_zh"]) or h.get("logo")

    hospitals_doc = {
        "version": 2,
        "note": "Bilingual hospital nodes; fill *_en via translate_whs_guide_en.py",
        "hospitals": hosp_list,
    }
    guide_doc = {
        "disease_id": "ORPHA:280",
        "slug": "whs",
        "name_en": "Wolf-Hirschhorn syndrome",
        "name_zh": "Wolf-Hirschhorn 综合征",
        "summary_zh": SUMMARY_ZH,
        "summary_en": prev_guide.get("summary_en") or SUMMARY_EN,
        "care_tips_zh": CARE_TIPS_ZH,
        "care_tips_en": prev_guide.get("care_tips_en") or CARE_TIPS_EN,
        "specialty_keywords_zh": KEYWORDS_ZH,
        "specialty_keywords_en": prev_guide.get("specialty_keywords_en") or KEYWORDS_EN,
        "experts": experts,
    }

    logo_map = {h["name_zh"]: existing_logos.get(h["name_zh"]) for h in hosp_list}

    (OUT_DIR / "hospitals.json").write_text(
        json.dumps(hospitals_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (GUIDES_DIR / "orpha-280.json").write_text(
        json.dumps(guide_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "hospital_logo_map.json").write_text(
        json.dumps(
            {
                "note": "Map hospital name (zh) -> filename under data/hospital-logos/.",
                "logos": logo_map,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    missing_en = sum(1 for e in experts if not e.get("name_en") or not e.get("bio_en"))
    print(f"✓ {len(hosp_list)} hospitals, {len(experts)} experts → data/")
    if missing_en:
        print(f"  ⚠ {missing_en} experts still missing EN — run translate_whs_guide_en.py")


if __name__ == "__main__":
    main()
