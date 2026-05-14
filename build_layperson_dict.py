#!/usr/bin/env python3
"""
build_layperson_dict.py — HPO term → layperson translation via LLM + deterministic dedup
========================================================================================
Phase 1: Translates ~19,944 Phenotype terms to patient-friendly phrases via LLM.
Phase 2: Deterministic collision resolution — no LLM, uses original HPO names for
         unique suffixes.  Per-group saves for crash resilience.

Usage:
  export DEEPSEEK_API_KEY="sk-..."
  python3 build_layperson_dict.py --model deepseek-v4-flash
  python3 build_layperson_dict.py --dry-run          # estimate cost
  python3 build_layperson_dict.py --resolve-only     # skip translation, only dedup
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Monkey-patch http.client to allow UTF-8 in HTTP header values
import http.client as _hc
_orig_putheader = _hc.HTTPConnection.putheader
def _utf8_putheader(self, header, *values):
    safe = []
    for v in values:
        if isinstance(v, str):
            try: safe.append(v.encode("latin-1"))
            except UnicodeEncodeError: safe.append(v.encode("utf-8"))
        else: safe.append(v)
    return _orig_putheader(self, header, *safe)
_hc.HTTPConnection.putheader = _utf8_putheader

# ----------------------------------------------------------------- Config
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
NODES_CSV = os.path.join(DATA_DIR, "nodes.csv")
OUTPUT_JSON = os.path.join(DATA_DIR, "layperson_dict.json")
LOG_FILE = os.path.join(DATA_DIR, "layperson_dict.log")
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# ----------------------------------------------------------------- Prompts
SYSTEM_PROMPT = (
    "You are a medical translator for a rare-disease symptom checker. "
    "Translate professional medical terms into simple, patient-friendly "
    "phrases (1-5 words).\n\n"
    "RULES:\n"
    "1. Use everyday language — no medical jargon.\n"
    "2. PRESERVE biological markers: enzyme names, gene symbols, proteins.\n"
    "3. Format: '[Level/Action] [Clinical Entity]'.\n"
    "4. Return ONLY the translated phrase. No quotes, no explanation."
)

FEW_SHOT_EXAMPLES = [
    ("Epileptic encephalopathy", "Severe seizures with brain effects"),
    ("Hypertonia", "Tight muscles"),
    ("Macrocephaly", "Large head size"),
    ("Multicystic kidney dysplasia", "Kidney cysts"),
    ("Progressive extrapyramidal muscular rigidity", "Worsening muscle stiffness"),
    ("Gastroesophageal reflux", "Acid reflux"),
    ("Failure to thrive", "Poor growth"),
    ("Generalized hypotonia", "Floppy muscles"),
    ("Decreased circulating acid maltase activity", "Low Acid Maltase (GAA) enzyme"),
    ("Elevated circulating creatine kinase concentration", "High CK enzyme levels"),
    ("Focal-onset seizure", "Seizures in one area"),
    ("Generalized-onset seizure", "Seizures across whole body"),
]

# ----------------------------------------------------------------- Helpers
def load_phenotype_terms(nodes_csv: str) -> Dict[str, str]:
    terms: Dict[str, str] = {}
    with open(nodes_csv, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("type", "").strip() == "Phenotype":
                terms[row["id"].strip()] = row.get("name", "").strip()
    print(f"📖 {len(terms):,} phenotype terms loaded")
    return terms

def load_existing_dict(path: str) -> Dict[str, str]:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_dict(path: str, data: Dict[str, str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def estimate_cost(num_terms: int, model: str, api_base: str, batch_size: int) -> None:
    prices = {"deepseek-chat": (0.14, 0.28), "deepseek-v4-flash": (0.14, 0.28),
              "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}
    in_p, out_p = prices.get(model, (0.50, 1.50))
    cost = (num_terms * 12 / 1e6) * in_p + (num_terms * 6 / 1e6) * out_p
    provider = "DeepSeek" if "deepseek" in api_base else "OpenAI"
    print(f"\n💵 Cost estimate ({model} @ {provider}): ${cost:.3f}")

def build_messages(hpo_name: str) -> list:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for med, lay in FEW_SHOT_EXAMPLES:
        msgs.append({"role": "user", "content": med})
        msgs.append({"role": "assistant", "content": lay})
    msgs.append({"role": "user", "content": hpo_name})
    return msgs

def detect_api_key(api_base: str) -> str:
    if "deepseek" in api_base:
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    else:
        key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("❌  API key not set.", file=sys.stderr)
        sys.exit(1)
    return key

# ----------------------------------------------------------------- API caller
def call_llm(api_base: str, api_key: str, model: str,
             messages: list, max_tokens: int = 25) -> Optional[str]:
    try: import requests
    except ImportError:
        print("❌  'requests' required", file=sys.stderr); sys.exit(1)
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1}
    try:
        resp = requests.post(url,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            timeout=(10, 60))
    except Exception as exc:
        print(f"   ⚠  Network error: {exc}", file=sys.stderr)
        return None
    if resp.status_code == 401:
        print(f"\n❌  API authentication failed (401). Check your API key.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code == 429: return None
    elif resp.status_code != 200:
        print(f"   ⚠  API error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None
    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip().strip('"').strip("'").rstrip(".")
    return content if content else None

def translate_single(api_base: str, api_key: str, model: str, hpo_name: str) -> Optional[str]:
    msgs = build_messages(hpo_name)
    for attempt in range(3):
        t = call_llm(api_base, api_key, model, msgs, max_tokens=25)
        if t: return t
        if attempt < 2: time.sleep([2, 5, 10][attempt])
    return None

# ----------------------------------------------------------------- Collision detection
def find_collisions(data: Dict[str, str]) -> List[Tuple[str, List[str]]]:
    t2ids: Dict[str, List[str]] = defaultdict(list)
    for hid, trans in data.items(): t2ids[trans].append(hid)
    return sorted(((t, ids) for t, ids in t2ids.items() if len(ids) > 1),
                  key=lambda x: -len(x[1]))

# ----------------------------------------------------------------- Deterministic dedup
def resolve_all_collisions(
    _api_base: str, _api_key: str, _model: str,
    data: Dict[str, str], all_terms: Dict[str, str],
    _max_rounds: int = 5
) -> Tuple[Dict[str, str], int]:
    """Deterministic collision resolution — no LLM calls."""
    collisions = find_collisions(data)
    if not collisions: return data, 0

    total_groups = len(collisions)
    total_dupes = sum(len(ids) - 1 for _, ids in collisions)
    print(f"\n🧹 Deterministic dedup: {total_groups} groups, {total_dupes} duplicates")
    print(f"   Using original HPO term keywords — no LLM calls needed\n")

    fixed = 0
    for gi, (shared_trans, hpo_ids) in enumerate(collisions):
        bar = "█" * int((gi / max(total_groups, 1)) * 20) + "░" * 20
        bar = bar[:20]
        desc = f"   [{bar}] {gi+1}/{total_groups} \"{shared_trans[:35]}\" ({len(hpo_ids)} terms)"
        print(f"\r{desc:<80}", end="", flush=True)

        used_in_group: Set[str] = set()
        changes = 0
        for hpo_id in hpo_ids:
            orig_name = all_terms.get(hpo_id, hpo_id)
            old = data[hpo_id]
            candidate = f"{shared_trans} ({orig_name})"
            data[hpo_id] = candidate
            used_in_group.add(candidate)
            changes += 1

        save_dict(OUTPUT_JSON, data)
        fixed += changes

        if gi % 50 == 0 or changes > 0:
            status = f"  ✓ {changes}/{len(hpo_ids)}" if changes else ""
            print(f"\r{' '*80}\r   [{bar}] {gi+1}/{total_groups} done{status}", flush=True)

    print(f"\r{' '*80}\r   ✅  All {total_groups} groups done: {fixed} entries fixed\n")
    return data, 1

# ----------------------------------------------------------------- Main
def main():
    parser = argparse.ArgumentParser(description="HPO → layperson translation + zero-collision dedup")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resolve-only", action="store_true",
                       help="Skip translation, only run deterministic dedup")
    args = parser.parse_args()

    all_terms = load_phenotype_terms(NODES_CSV)
    if not all_terms: print("❌  No phenotype terms", file=sys.stderr); sys.exit(1)

    existing = load_existing_dict(OUTPUT_JSON)
    if existing:
        print(f"📂 Loaded existing: {len(existing):,} entries ({len(set(existing.values())):,} unique)")

    remaining = {k: v for k, v in all_terms.items() if k not in existing}
    if args.dry_run:
        estimate_cost(len(remaining), args.model, args.api_base, args.batch_size)
        if existing:
            c = find_collisions(existing)
            if c: print(f"   ⚠  {sum(len(ids) for _, ids in c)} collisions — use --resolve-only to fix")
        return

    api_key = detect_api_key(args.api_base)
    provider = "DeepSeek" if "deepseek" in args.api_base else "OpenAI"
    result = dict(existing)

    # # --- Phase 1: Translation ---
    # if not args.resolve_only and remaining:
    #     print(f"\n{'='*60}\n PHASE 1 — Translation ({args.model} @ {provider})\n{'='*60}")
    #     print(f"🤖 Translating {len(remaining):,} terms (batch: {args.batch_size})\n")
    #     items = list(remaining.items())
    #     skipped = 0
    #     for i, (hpo_id, hpo_name) in enumerate(items):
    #         if i % args.batch_size == 0 and i > 0:
    #             done = len(result) - len(existing)
    #             u = len(set(result.values()))
    #             c = sum(len(ids)-1 for _, ids in find_collisions(result))
    #             print(f"   ... {done}/{len(remaining)} | unique: {u} | collisions: {c}", flush=True)
    #         t = translate_single(args.api_base, api_key, args.model, hpo_name)
    #         if t: result[hpo_id] = t
    #         else: skipped += 1
    #         if (i+1) % args.batch_size == 0:
    #             save_dict(OUTPUT_JSON, result)
    #     save_dict(OUTPUT_JSON, result)
    #     u = len(set(result.values()))
    #     print(f"\n   ✅  Phase 1: {len(result):,} total, {u:,} unique, {skipped} failed")

    # elif args.resolve_only:
    #     print(f"\n   ⏭  Skipping translation (--resolve-only)")
    # import concurrent.futures

    import concurrent.futures

    # --- Phase 1: Translation ---
    if not args.resolve_only and remaining:
        print(f"\n{'='*60}\n PHASE 1 — Translation ({args.model} @ {provider})\n{'='*60}")
        items = list(remaining.items())
        
        # 【修改点 1】：必须要初始化 skipped 变量！
        skipped = 0 
        
        # 使用 10 个线程并发请求（DeepSeek 可以开到 20）
        MAX_WORKERS = 10 
        
        def process_item(item):
            hpo_id, hpo_name = item
            t = translate_single(args.api_base, api_key, args.model, hpo_name)
            return hpo_id, t

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_item, item): item for item in items}
            
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                hpo_id, t = future.result()
                if t: 
                    result[hpo_id] = t
                else: 
                    skipped += 1
                
                # 每 100 个保存一次，防止频繁写盘
                if (i + 1) % 100 == 0:
                    # 【修改点 2】：打印时顺便输出一下 skipped 的数量，方便你监控健康度
                    print(f"   ... {len(result)}/{len(all_terms)} completed (Failed: {skipped})", flush=True)
                    save_dict(OUTPUT_JSON, result)
                    
        save_dict(OUTPUT_JSON, result)
        print(f"\n   ✅  Phase 1 Completed: {len(result):,} total translated, {skipped} failed")



    # --- Phase 2: Deterministic dedup ---
    collisions_before = find_collisions(result)
    if collisions_before:
        print(f"\n{'='*60}\n PHASE 2 — Deterministic Dedup\n{'='*60}")
        print(f"🔍 {sum(len(ids) for _, ids in collisions_before)} duplicates "
              f"in {len(collisions_before)} groups")
        result, _ = resolve_all_collisions(args.api_base, api_key, args.model,
                                           result, all_terms)
        save_dict(OUTPUT_JSON, result)
    else:
        print(f"\n🎉  Zero collisions!")

    # --- Final ---
    unique_count = len(set(result.values()))
    print(f"\n{'='*60}")
    if unique_count == len(result):
        print(f" 🎉  PERFECT — zero collisions!")
    else:
        print(f" ⚠  {len(result) - unique_count} collisions remain")
    print(f"     Terms:  {len(result):,}")
    print(f"     Unique: {unique_count:,}")
    print(f"     Output: {OUTPUT_JSON}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
