#!/usr/bin/env python3
"""
build_disease_dict_zh.py — 中文疾病名称翻译
=============================================
从 nodes.csv 提取所有 Disease 节点，通过 DeepSeek API 批量翻译为中文。
输出: disease_names_zh.json  (ORPHA:ID → 中文疾病名)

用法:
  export DEEPSEEK_API_KEY="sk-..."
  python3 build_disease_dict_zh.py
  python3 build_disease_dict_zh.py --dry-run     # 估算费用
  python3 build_disease_dict_zh.py --workers 20  # 并发数
"""

import argparse
import csv
import json
import os
import sys
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Monkey-patch http.client to allow UTF-8 in HTTP header values
import http.client as _hc

_orig_putheader = _hc.HTTPConnection.putheader

def _utf8_putheader(self, header, *values):
    safe = []
    for v in values:
        if isinstance(v, str):
            try:
                safe.append(v.encode("latin-1"))
            except UnicodeEncodeError:
                safe.append(v.encode("utf-8"))
        else:
            safe.append(v)
    return _orig_putheader(self, header, *safe)

_hc.HTTPConnection.putheader = _utf8_putheader

# ----------------------------------------------------------------- Config
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
NODES_CSV = os.path.join(DATA_DIR, "nodes.csv")
DISEASE_ZH_JSON = os.path.join(DATA_DIR, "disease_names_zh.json")
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
CHECKPOINT_INTERVAL = 200

# ----------------------------------------------------------------- Prompt
SYSTEM_PROMPT = (
    "你是一位罕见病术语翻译专家。将英文疾病名称翻译为规范的中文名称。\n\n"
    "规则：\n"
    "1. 使用规范的中文医学名词。\n"
    "2. 保留基因符号、蛋白名称、罗马数字（如 GAA、COL4A5、Type II）。\n"
    "3. 综合征 → 综合征（不用症候群）。\n"
    "4. 只输出翻译结果，不加引号、不加解释。\n"
    "5. 尽量精简（2-20 字）。"
)

FEW_SHOT = [
    ("Ataxia-telangiectasia", "共济失调毛细血管扩张症"),
    ("Fabry disease", "法布里病"),
    ("Gaucher disease", "戈谢病"),
    ("Pompe disease", "庞贝病"),
    ("Niemann-Pick disease type C", "尼曼-匹克病C型"),
    ("Duchenne muscular dystrophy", "杜氏肌营养不良症"),
    ("Spinal muscular atrophy", "脊髓性肌萎缩症"),
    ("Huntington disease", "亨廷顿病"),
    ("Marfan syndrome", "马凡综合征"),
    ("Ehlers-Danlos syndrome", "埃勒斯-当洛斯综合征"),
    ("Osteogenesis imperfecta", "成骨不全症"),
    ("Retinitis pigmentosa", "视网膜色素变性"),
    ("Polycystic kidney disease", "多囊肾病"),
    ("Alport syndrome", "Alport综合征"),
    ("Wilson disease", "肝豆状核变性"),
    ("Hemophilia A", "血友病A"),
    ("Thalassemia", "地中海贫血"),
    ("Cystic fibrosis", "囊性纤维化"),
    ("Phenylketonuria", "苯丙酮尿症"),
    ("Maple syrup urine disease", "枫糖尿症"),
    ("Acute intermittent porphyria", "急性间歇性卟啉病"),
    ("Systemic lupus erythematosus", "系统性红斑狼疮"),
    ("Amyotrophic lateral sclerosis", "肌萎缩侧索硬化症"),
    ("Multiple sclerosis", "多发性硬化"),
    ("Myasthenia gravis", "重症肌无力"),
]

def load_disease_terms(csv_path: str) -> List[Tuple[str, str]]:
    """Load all Disease nodes from nodes.csv. Returns [(disease_id, name), ...]."""
    terms: List[Tuple[str, str]] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            node_type = row[2].strip()
            if node_type == "Disease":
                disease_id = row[0].strip()
                name = row[1].strip()
                terms.append((disease_id, name))
    return terms

def load_existing_dict(path: str) -> Dict[str, str]:
    """Load existing JSON dictionary if it exists."""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_dict(path: str, data: Dict[str, str]):
    """Save dictionary to JSON file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def detect_api_key(api_base: str) -> str:
    """Get API key from environment."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key and "openai" in api_base.lower():
        key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        print("❌ 请设置 DEEPSEEK_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)
    return key

def detect_proxy() -> Optional[Dict[str, str]]:
    """Auto-detect Clash proxy or use env vars."""
    for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        val = os.environ.get(var, "")
        if val:
            return {"http": val, "https": val}
    # Try auto-detecting Clash
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 7890))
        s.close()
        proxy_url = "http://127.0.0.1:7890"
        print(f"🔍 自动检测到 Clash 代理: {proxy_url}")
        return {"http": proxy_url, "https": proxy_url}
    except (socket.timeout, ConnectionRefusedError, OSError):
        pass
    return None

def call_llm(api_base: str, api_key: str, model: str, disease_name: str,
             proxies: Optional[Dict[str, str]] = None,
             max_retries: int = 3) -> Optional[str]:
    """Translate a single disease name to Chinese."""
    import requests as _req

    few_shot_msgs = []
    for en, zh in FEW_SHOT:
        few_shot_msgs.append({"role": "user", "content": en})
        few_shot_msgs.append({"role": "assistant", "content": zh})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *few_shot_msgs,
        {"role": "user", "content": disease_name},
    ]

    for attempt in range(max_retries):
        try:
            r = _req.post(
                f"{api_base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": messages, "max_tokens": 50, "temperature": 0.1},
                timeout=(10, 30),
                proxies=proxies,
            )
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()
            elif r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)
    return None

def translate_batch(
    api_base: str, api_key: str, model: str,
    batch: List[Tuple[str, str]],
    output_path: str,
    lock: threading.Lock,
    counters: Dict[str, int],
    total: int,
    start_time: float,
    proxies: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Translate a batch of disease names."""
    results = {}
    for disease_id, name in batch:
        zh_name = call_llm(api_base, api_key, model, name, proxies)
        if zh_name:
            results[disease_id] = zh_name
            with lock:
                counters["ok"] += 1
        else:
            results[disease_id] = name  # fallback: keep English
            with lock:
                counters["fail"] += 1

        # Progress
        with lock:
            done = counters["skip"] + counters["ok"] + counters["fail"]
            if done % 50 == 0 or done == total:
                pct = done / max(total, 1) * 100
                elapsed = time.time() - start_time
                rate = done / max(elapsed, 1)
                eta = (total - done) / max(rate, 0.01)
                eta_str = f"{int(eta//60)}m{int(eta%60)}s" if eta < 3600 else f"{eta/3600:.1f}h"
                bar_len = 30
                filled = int(bar_len * done / total)
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"\r  [{bar}] {pct:5.1f}% ({done:,}/{total:,}) "
                      f"✅{counters['ok']} ❌{counters['fail']} ETA {eta_str}",
                      end="", flush=True)
    return results

def estimate_cost(count: int, model: str, api_base: str):
    """Estimate DeepSeek API cost."""
    # DeepSeek-chat: ~$0.27/M input tokens, ~$1.10/M output tokens
    # Each request: ~50 tokens input (system + few-shot + disease name) + ~10 tokens output
    input_tokens = count * 300   # ~300 input tokens per request with few-shot
    output_tokens = count * 15   # ~15 output tokens per request
    input_cost = input_tokens / 1_000_000 * 0.27
    output_cost = output_tokens / 1_000_000 * 1.10
    total = input_cost + output_cost
    print(f"📊 费用估算:")
    print(f"   疾病数量: {count:,}")
    print(f"   输入 tokens: ~{input_tokens:,} → ${input_cost:.3f}")
    print(f"   输出 tokens: ~{output_tokens:,} → ${output_cost:.3f}")
    print(f"   总计: ~${total:.3f}")
    if "deepseek" in api_base.lower():
        print(f"   (DeepSeek 夜间 00:00-08:00 有折扣)")

def find_collisions(data: Dict[str, str]) -> List[Tuple[str, List[str]]]:
    """Find duplicate values in dictionary."""
    groups = defaultdict(list)
    for k, v in data.items():
        groups[v].append(k)
    return [(val, ids) for val, ids in groups.items() if len(ids) > 1]

def main():
    parser = argparse.ArgumentParser(description="中文疾病名称翻译")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=15, help="并发线程数")
    parser.add_argument("--dry-run", action="store_true", help="估算费用")
    args = parser.parse_args()

    # Load diseases
    disease_terms = load_disease_terms(NODES_CSV)
    if not disease_terms:
        print("❌ 未找到疾病节点", file=sys.stderr)
        sys.exit(1)
    print(f"📂 从 nodes.csv 读取 {len(disease_terms):,} 个疾病名称")

    if args.dry_run:
        existing = load_existing_dict(DISEASE_ZH_JSON)
        remaining = len(disease_terms) - len(existing)
        print(f"   已有翻译: {len(existing):,} 条")
        print(f"   待翻译: {remaining:,} 条")
        estimate_cost(remaining, args.model, args.api_base)
        return

    api_key = detect_api_key(args.api_base)
    proxies = detect_proxy()

    # Connection test
    import requests as _req
    ok = False
    try:
        r = _req.post(
            f"{args.api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": args.model, "messages": [{"role": "user", "content": "Translate to Chinese: Fever"}], "max_tokens": 10},
            timeout=(10, 30),
            proxies=proxies,
        )
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"].strip()
            print(f"🔌 API 连接测试: ✅ ({content})")
            ok = True
        else:
            print(f"🔌 HTTP {r.status_code}: {r.text[:300]}")
    except Exception as e:
        print(f"🔌 网络异常: {type(e).__name__}: {e}")

    if not ok:
        print(f"\n❌ API 连接失败！")
        print(f"   → 检查 DEEPSEEK_API_KEY 和代理设置")
        sys.exit(1)
    print()

    # Load existing translations
    existing = load_existing_dict(DISEASE_ZH_JSON)
    if existing:
        print(f"📂 已有翻译: {len(existing):,} 条 (增量模式)")

    # Filter out already-translated (only count as done if the value ≠ English original)
    to_translate = []
    for did, name in disease_terms:
        existing_val = existing.get(did, "")
        if existing_val and existing_val != name:  # has real translation (not failed fallback)
            continue
        to_translate.append((did, name))
    print(f"📝 待翻译: {len(to_translate):,} 条 (含 {sum(1 for did, name in to_translate if did in existing)} 条重试)\n")

    if not to_translate:
        print("✅ 全部完成，无需翻译")
        return

    # Batch processing with thread pool
    batch_size = max(1, len(to_translate) // (args.workers * 4))
    batches = [to_translate[i:i+batch_size] for i in range(0, len(to_translate), batch_size)]

    lock = threading.Lock()
    already_done = len(disease_terms) - len(to_translate)
    counters = {"ok": 0, "fail": 0, "skip": already_done}
    start_time = time.time()
    total = len(disease_terms)

    results = {did: val for did, val in existing.items() if val != dict(disease_terms).get(did, "")}
    checkpoint_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                translate_batch, args.api_base, api_key, args.model,
                batch, DISEASE_ZH_JSON,
                lock, counters, total, start_time, proxies,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            batch_results = future.result()
            results.update(batch_results)
            checkpoint_count += len(batch_results)
            if checkpoint_count >= CHECKPOINT_INTERVAL:
                save_dict(DISEASE_ZH_JSON, results)
                checkpoint_count = 0

    # Final save
    save_dict(DISEASE_ZH_JSON, results)
    print()

    elapsed = time.time() - start_time
    rate = len(to_translate) / max(elapsed, 1)
    print(f"\n{'='*60}")
    print(f" 📊 完成统计")
    print(f"{'='*60}")
    print(f"   总计: {len(results):,} 条")
    print(f"   成功: {counters['ok']:,} ({counters['ok']/max(len(to_translate),1)*100:.1f}%)")
    print(f"   失败: {counters['fail']:,}")
    print(f"   耗时: {elapsed:.0f}s ({rate:.1f} 条/秒)")
    print(f"   输出: {DISEASE_ZH_JSON}")

    # Check for collisions
    collisions = find_collisions(results)
    if collisions:
        dupes = sum(len(ids) for _, ids in collisions)
        print(f"   ⚠  {dupes} 个重复翻译 ({len(collisions)} 组) — 不同英文名翻译成相同中文")
        print(f"   例: {collisions[:3]}")
    else:
        print(f"   ✅ 零碰撞")

if __name__ == "__main__":
    main()
