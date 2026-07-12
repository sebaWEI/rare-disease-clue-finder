#!/usr/bin/env python3
"""Translate WHS care-guide Chinese fields into English (DeepSeek).

Writes progress after every batch so re-runs resume cleanly.

Usage:
  python3 scripts/translate_whs_guide_en.py
  python3 scripts/translate_whs_guide_en.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

from openai import OpenAI  # noqa: E402

HOSPITALS_PATH = ROOT / "data" / "hospitals.json"
GUIDE_PATH = ROOT / "data" / "guides" / "orpha-280.json"

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

HOSPITAL_EN = {
    "中国医学科学院北京协和医院": "Peking Union Medical College Hospital",
    "北京大学第三医院": "Peking University Third Hospital",
    "北京大学第一医院": "Peking University First Hospital",
    "天津儿童医院": "Tianjin Children's Hospital",
    "中国医科大学附属盛京医院": "Shengjing Hospital of China Medical University",
    "哈尔滨医科大学附属第一医院": "The First Affiliated Hospital of Harbin Medical University",
    "上海交通大学医学院附属新华医院": "Xinhua Hospital Affiliated to Shanghai Jiao Tong University School of Medicine",
    "上海交通大学医学院附属瑞金医院": "Ruijin Hospital, Shanghai Jiao Tong University School of Medicine",
    "上海交通大学医学院附属上海儿童医学中心": "Shanghai Children's Medical Center",
    "南京医科大学附属儿童医院": "Children's Hospital of Nanjing Medical University",
    "浙江大学医学院附属儿童医院": "Children's Hospital, Zhejiang University School of Medicine",
    "江西省儿童医院/江西省妇幼保健院": "Jiangxi Provincial Children's Hospital / Jiangxi Maternal and Child Health Hospital",
    "广州市妇女儿童医疗中心": "Guangzhou Women and Children's Medical Center",
    "中山大学附属第一医院": "The First Affiliated Hospital, Sun Yat-sen University",
    "南方医科大学南方医院": "Nanfang Hospital, Southern Medical University",
    "广州市妇女儿童医疗中心柳州医院": "Guangzhou Women and Children's Medical Center Liuzhou Hospital",
    "四川大学华西医院": "West China Hospital, Sichuan University",
    "四川大学华西第二医院/四川大学妇产儿童医院": "West China Second University Hospital / Sichuan University Women and Children's Hospital",
    "重庆医科大学附属儿童医院": "Children's Hospital of Chongqing Medical University",
    "西安交通大学第一附属医院": "The First Affiliated Hospital of Xi'an Jiaotong University",
    "华中科技大学同济医学院附属同济医院": "Tongji Hospital, Tongji Medical College of HUST",
    "河南省儿童医院": "Henan Children's Hospital",
    "中南大学湘雅医院": "Xiangya Hospital, Central South University",
}

SUMMARY_EN = (
    "A rare genetic condition caused by a deletion on the short arm of "
    "chromosome 4, often featuring growth delay, characteristic facial "
    "features, epilepsy, and multi-system involvement. This page offers "
    "patient-oriented care navigation and specialist information — not "
    "medical advice."
)

CARE_TIPS_EN = [
    "Prioritise centres with rare-disease clinics, genetics / metabolic medicine, developmental paediatrics, paediatric neurology, or rehabilitation.",
    "Bring existing genetic reports, growth records, and epilepsy-related tests to support multidisciplinary assessment.",
    "Nearest ranking is estimated by province/city centre — always confirm booking via official hospital channels.",
]

KEYWORDS_EN = [
    "Rare disease",
    "Genetics / metabolism",
    "Developmental paediatrics",
    "Paediatric neurology",
    "Rehabilitation",
]


def client() -> OpenAI:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key or key.startswith("sk-your"):
        raise SystemExit("DEEPSEEK_API_KEY missing — set it in .env")
    return OpenAI(api_key=key, base_url="https://api.deepseek.com")


def translate_batch(cli: OpenAI, items: list[dict], kind: str) -> dict[str, str]:
    if not items:
        return {}
    payload = [{"id": it["id"], "text": it["text"]} for it in items]
    system = (
        "You translate Chinese medical care-guide text into clear British/"
        "international English for rare-disease patients and families. "
        "Keep clinical meaning accurate. Preserve person names in pinyin "
        "or established English forms; keep hospital/department acronyms. "
        "Return ONLY valid JSON: an array of {\"id\": string, \"en\": string} "
        "with the same ids, no markdown."
    )
    user = f"Translate each {kind} field to English.\n\n" + json.dumps(
        payload, ensure_ascii=False
    )
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = cli.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            data = json.loads(raw)
            out = {}
            for row in data:
                rid = str(row.get("id") or "")
                en = str(row.get("en") or "").strip()
                if rid and en:
                    out[rid] = en
            if out:
                return out
            print(f"   ⚠ empty batch for {kind}, retry…")
        except Exception as exc:
            last_err = exc
            print(f"   ⚠ {kind} attempt {attempt + 1} failed: {exc}")
            time.sleep(2.0 * (attempt + 1))
    raise SystemExit(f"Failed to translate {kind}: {last_err}")


def chunked(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def migrate_hospital(h: dict) -> dict:
    name_zh = h.get("name_zh") or h.get("name") or ""
    city_zh = h.get("city_zh") or h.get("city") or ""
    adv_zh = h.get("advantage_zh") or h.get("advantage") or ""
    return {
        "id": h["id"],
        "name_zh": name_zh,
        "name_en": h.get("name_en") or HOSPITAL_EN.get(name_zh) or name_zh,
        "city_zh": city_zh,
        "city_en": h.get("city_en") or CITY_EN.get(city_zh) or city_zh,
        "lat": h.get("lat"),
        "lng": h.get("lng"),
        "logo": h.get("logo"),
        "advantage_zh": adv_zh,
        "advantage_en": h.get("advantage_en") or "",
    }


def migrate_expert(e: dict) -> dict:
    name_zh = e.get("name_zh") or e.get("name") or ""
    bio_zh = e.get("bio_zh") or e.get("bio") or ""
    return {
        "id": e["id"],
        "name_zh": name_zh,
        "name_en": e.get("name_en") or "",
        "type": e.get("type") or "doctor",
        "hospital_id": e.get("hospital_id"),
        "bio_zh": bio_zh,
        "bio_en": e.get("bio_en") or "",
        "disease_ids": list(e.get("disease_ids") or ["ORPHA:280"]),
    }


def save(hospitals: list, guide: dict) -> None:
    HOSPITALS_PATH.write_text(
        json.dumps(
            {
                "version": 2,
                "note": "Bilingual hospital nodes; logo from hospital_logo_map.json",
                "hospitals": hospitals,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    GUIDE_PATH.write_text(
        json.dumps(guide, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    hosp_doc = json.loads(HOSPITALS_PATH.read_text(encoding="utf-8"))
    guide = json.loads(GUIDE_PATH.read_text(encoding="utf-8"))

    hospitals = [migrate_hospital(h) for h in hosp_doc.get("hospitals") or []]
    experts = [migrate_expert(e) for e in guide.get("experts") or []]

    guide["summary_zh"] = guide.get("summary_zh") or guide.get("summary") or ""
    if args.force or not guide.get("summary_en"):
        guide["summary_en"] = SUMMARY_EN
    guide["care_tips_zh"] = guide.get("care_tips_zh") or guide.get("care_tips") or []
    if args.force or not guide.get("care_tips_en"):
        guide["care_tips_en"] = CARE_TIPS_EN
    guide["specialty_keywords_zh"] = (
        guide.get("specialty_keywords_zh") or guide.get("specialty_keywords") or []
    )
    if args.force or not guide.get("specialty_keywords_en"):
        guide["specialty_keywords_en"] = KEYWORDS_EN
    for k in ("summary", "care_tips", "specialty_keywords"):
        guide.pop(k, None)
    guide["experts"] = experts
    save(hospitals, guide)
    print("✓ migrated bilingual schema")

    cli = client()

    need_adv = [
        {"id": h["id"], "text": h["advantage_zh"]}
        for h in hospitals
        if h["advantage_zh"] and (args.force or not h.get("advantage_en"))
    ]
    print(f"Translating {len(need_adv)} hospital advantages…")
    for batch in chunked(need_adv, 4):
        got = translate_batch(cli, batch, "hospital advantage")
        by_id = {h["id"]: h for h in hospitals}
        for hid, en in got.items():
            by_id[hid]["advantage_en"] = en
        save(hospitals, guide)
        print(f"  ✓ {len(got)} advantages (saved)")
        time.sleep(0.3)

    need_names = [
        {"id": e["id"], "text": e["name_zh"]}
        for e in experts
        if e["name_zh"] and (args.force or not e.get("name_en"))
    ]
    print(f"Translating {len(need_names)} expert names…")
    for batch in chunked(need_names, 10):
        got = translate_batch(cli, batch, "expert/department/team name")
        by_id = {e["id"]: e for e in experts}
        for eid, en in got.items():
            by_id[eid]["name_en"] = en
        guide["experts"] = experts
        save(hospitals, guide)
        print(f"  ✓ {len(got)} names (saved)")
        time.sleep(0.3)

    need_bios = [
        {"id": e["id"], "text": e["bio_zh"]}
        for e in experts
        if e["bio_zh"] and (args.force or not e.get("bio_en"))
    ]
    print(f"Translating {len(need_bios)} expert bios (1 at a time)…")
    for i, item in enumerate(need_bios, 1):
        got = translate_batch(cli, [item], "expert biography")
        by_id = {e["id"]: e for e in experts}
        for eid, en in got.items():
            by_id[eid]["bio_en"] = en
        guide["experts"] = experts
        save(hospitals, guide)
        print(f"  ✓ bio {i}/{len(need_bios)} ({item['id']})")
        time.sleep(0.25)

    missing_adv = sum(1 for h in hospitals if h["advantage_zh"] and not h["advantage_en"])
    missing_bio = sum(1 for e in experts if e["bio_zh"] and not e["bio_en"])
    missing_name = sum(1 for e in experts if e["name_zh"] and not e["name_en"])
    print(
        f"Done. Missing EN — advantages:{missing_adv} names:{missing_name} bios:{missing_bio}"
    )


if __name__ == "__main__":
    main()
