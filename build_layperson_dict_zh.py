#!/usr/bin/env python3
"""
build_layperson_dict_zh.py — 中文版 HPO 术语翻译 + 通俗化映射
=============================================================
生成两个文件：
  Phase 1a: hpo_names_zh.json     — HPO ID → 中文专业术语（用于专家模式）
  Phase 1b: layperson_dict_zh.json — HPO ID → 中文通俗说法（用于公众模式）
  Phase 2:  Deterministic 去重      — 确保最终无重复词条

用法:
  export DEEPSEEK_API_KEY="sk-..."
  python3 build_layperson_dict_zh.py --mode both         # 生成两份字典
  python3 build_layperson_dict_zh.py --mode medical      # 仅生成中文专业术语
  python3 build_layperson_dict_zh.py --mode layperson    # 仅生成中文通俗说法
  python3 build_layperson_dict_zh.py --dry-run           # 估算费用
  python3 build_layperson_dict_zh.py --resolve-only      # 仅对已有 layperson 字典去重
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
HPO_NAMES_ZH_JSON = os.path.join(DATA_DIR, "hpo_names_zh.json")
LAYERSON_ZH_JSON = os.path.join(DATA_DIR, "layperson_dict_zh.json")
LOG_FILE = os.path.join(DATA_DIR, "layperson_dict_zh.log")
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# ----------------------------------------------------------------- 中文 Prompt 配置
# ── 医学术语 prompt（用于生成 hpo_names_zh.json）──
MEDICAL_SYSTEM_PROMPT = (
    "你是一位罕见病术语翻译专家。将英文 HPO 医学术语翻译为中文专业术语。\n\n"
    "规则：\n"
    "1. 使用规范的中文医学名词（参考《医学名词》审定标准）。\n"
    "2. 保留生物标志物原名：酶名（如 GAA、CK）、基因符号、蛋白名称。\n"
    "3. 只输出翻译结果，不加引号、不加解释。\n"
    "4. 尽量精简（2-8 字）。"
)

MEDICAL_FEW_SHOT = [
    ("Multicystic kidney dysplasia", "多囊性肾发育不良"),
    ("Epileptic encephalopathy", "癫痫性脑病"),
    ("Hypertonia", "肌张力过高"),
    ("Macrocephaly", "巨头畸形"),
    ("Gastroesophageal reflux", "胃食管反流"),
    ("Failure to thrive", "生长迟缓"),
    ("Generalized hypotonia", "全身性肌张力低下"),
    ("Focal-onset seizure", "局灶性癫痫发作"),
    ("Generalized-onset seizure", "全面性癫痫发作"),
    ("Progressive extrapyramidal muscular rigidity", "进行性锥体外系肌强直"),
    ("Decreased circulating acid maltase activity", "血酸性麦芽糖酶(GAA)活性降低"),
    ("Elevated circulating creatine kinase concentration", "血肌酸激酶(CK)升高"),
]

# ── 通俗说法 prompt（用于生成 layperson_dict_zh.json）──
LAYPERSON_SYSTEM_PROMPT = (
    "你是一位罕见病科普翻译专家。将英文医学术语翻译为患者能看懂的中文通俗说法。\n\n"
    "规则：\n"
    "1. 用日常语言，不使用任何专业术语。就像在跟一位初中文化的患者解释。\n"
    "2. 保留生物标志物原名：酶名（如 GAA、CK）、基因符号、蛋白名称。\n"
    "3. 只用 1-6 个汉字，越简短越好。\n"
    "4. 只输出翻译结果，不加引号、不加解释。\n"
    "5. 避免使用「异常」「异常性」等笼统词——说「骨头软」而不是「骨骼异常」。"
)

LAYPERSON_FEW_SHOT = [
    ("Multicystic kidney dysplasia", "肾囊肿"),
    ("Epileptic encephalopathy", "严重癫痫"),
    ("Hypertonia", "肌肉紧绷"),
    ("Macrocephaly", "头围过大"),
    ("Gastroesophageal reflux", "胃酸反流"),
    ("Failure to thrive", "发育迟缓"),
    ("Generalized hypotonia", "四肢松软"),
    ("Focal-onset seizure", "局部抽搐"),
    ("Generalized-onset seizure", "全身抽搐"),
    ("Seizure", "抽搐"),
    ("Fever", "发热"),
    ("Abdominal pain", "腹痛"),
    ("Progressive extrapyramidal muscular rigidity", "肌肉逐渐僵硬"),
    ("Decreased circulating acid maltase activity", "GAA酶偏低"),
    ("Elevated circulating creatine kinase concentration", "肌酸激酶偏高"),
    ("Neurogenic bladder", "神经性排尿困难"),
    ("Recurrent urinary tract infections", "反复尿路感染"),
    ("Abnormality of body height", "身高异常"),
    ("Autosomal dominant inheritance", "父母一方患病即遗传"),
    ("Autosomal recessive inheritance", "父母双方携带才发病"),
]


# ----------------------------------------------------------------- Helpers
def load_phenotype_terms(nodes_csv: str) -> Dict[str, str]:
    terms: Dict[str, str] = {}
    with open(nodes_csv, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("type", "").strip() == "Phenotype":
                terms[row["id"].strip()] = row.get("name", "").strip()
    print(f"📖 {len(terms):,} 个表型术语已加载")
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


def estimate_cost(num_terms: int, model: str, api_base: str, passes: int = 1) -> None:
    prices = {
        "deepseek-chat": (0.14, 0.28),
        "deepseek-v4-flash": (0.14, 0.28),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
    }
    in_p, out_p = prices.get(model, (0.50, 1.50))
    # Chinese output ≈ 5 tokens per term (vs English ~6)
    cost = (num_terms * 15 / 1e6) * in_p + (num_terms * 5 / 1e6) * out_p
    cost *= passes
    provider = "DeepSeek" if "deepseek" in api_base else "OpenAI"
    print(f"\n💵 费用估算 ({model} @ {provider}): ${cost:.3f} (×{passes} passes)")


def build_messages(hpo_name: str, system_prompt: str, few_shot: List[Tuple[str, str]]) -> list:
    msgs = [{"role": "system", "content": system_prompt}]
    for med, translation in few_shot:
        msgs.append({"role": "user", "content": med})
        msgs.append({"role": "assistant", "content": translation})
    msgs.append({"role": "user", "content": hpo_name})
    return msgs


def detect_api_key(api_base: str) -> str:
    if "deepseek" in api_base:
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    else:
        key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("❌  API key 未设置.", file=sys.stderr)
        print("   export DEEPSEEK_API_KEY=\"sk-...\"", file=sys.stderr)
        sys.exit(1)
    return key


def detect_proxy() -> Optional[Dict[str, str]]:
    """Auto-detect HTTP proxy for API calls.

    Priority: env vars → Clash auto-detect on 7890 → None.
    """
    # 1. Check standard proxy env vars
    for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        val = os.environ.get(var, "")
        if val:
            print(f"🔌 代理检测: {var}={val}")
            return {"http": val, "https": val}

    # 2. Auto-detect Clash on localhost:7890
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 7890))
        s.close()
        proxy_url = "http://127.0.0.1:7890"
        print(f"🔌 代理检测: Clash 自动发现 @ {proxy_url}")
        return {"http": proxy_url, "https": proxy_url}
    except Exception:
        pass
    finally:
        s.close()

    return None


def call_llm(api_base: str, api_key: str, model: str,
             messages: list, max_tokens: int = 20, proxies=None) -> Optional[str]:
    try:
        import requests
    except ImportError:
        print("❌  'requests' 未安装", file=sys.stderr)
        sys.exit(1)
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            json=payload,
            timeout=(10, 60),
            proxies=proxies,
        )
    except Exception as exc:
        print(f"   ⚠  网络错误: {exc}", file=sys.stderr)
        return None
    if resp.status_code == 401:
        print("\n❌  API 认证失败 (401)，请检查 API key", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code == 429:
        return None
    elif resp.status_code != 200:
        print(f"   ⚠  API 错误 {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None
    data = resp.json()
    message = data["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    # deepseek-v4-flash 是推理模型，输出在 reasoning_content
    if not content:
        content = (message.get("reasoning_content") or "").strip()
    content = content.strip('"').strip("'").rstrip("。").rstrip(".")
    return content if content else None


def translate_single(
    api_base: str, api_key: str, model: str,
    hpo_name: str, system_prompt: str, few_shot: List[Tuple[str, str]],
    proxies=None,
) -> Optional[str]:
    msgs = build_messages(hpo_name, system_prompt, few_shot)
    for attempt in range(3):
        t = call_llm(api_base, api_key, model, msgs, max_tokens=20, proxies=proxies)
        if t:
            return t
        if attempt < 2:
            time.sleep([2, 5, 10][attempt])
    return None


# ----------------------------------------------------------------- Collision detection
def find_collisions(data: Dict[str, str]) -> List[Tuple[str, List[str]]]:
    t2ids: Dict[str, List[str]] = defaultdict(list)
    for hid, trans in data.items():
        t2ids[trans].append(hid)
    return sorted(
        ((t, ids) for t, ids in t2ids.items() if len(ids) > 1),
        key=lambda x: -len(x[1]),
    )


# ----------------------------------------------------------------- Deterministic dedup（中文版 — 专业术语）
def resolve_medical_collisions_zh(
    data: Dict[str, str],
    all_terms_en: Dict[str, str],
    output_path: str,
) -> Dict[str, str]:
    """
    对中文专业术语字典去重。
    策略：重复术语后缀加上英文 HPO 原名（保证唯一）。
    必须在构建 layperson 字典之前运行，否则 Phase 2 的后缀来源不唯一。
    """
    collisions = find_collisions(data)
    if not collisions:
        return data

    total_groups = len(collisions)
    total_dupes = sum(len(ids) - 1 for _, ids in collisions)
    print(f"\n🧹 医学术语去重: {total_groups} 组冲突, {total_dupes} 个重复")
    print(f"   策略: 重复术语后缀加英文 HPO 原名作为区分\n")

    fixed = 0
    for gi, (shared_trans, hpo_ids) in enumerate(collisions):
        bar = "█" * int((gi / max(total_groups, 1)) * 20) + "░" * 20
        bar = bar[:20]
        desc = (
            f"   [{bar}] {gi+1}/{total_groups} "
            f"'{shared_trans[:25]}' ({len(hpo_ids)} 个术语)"
        )
        print(f"\r{desc:<80}", end="", flush=True)

        for hpo_id in hpo_ids:
            suffix = all_terms_en.get(hpo_id, hpo_id)
            data[hpo_id] = f"{shared_trans}（{suffix}）"
            fixed += 1

        save_dict(output_path, data)

        if gi % 50 == 0:
            print(f"\r{' '*80}\r   [{bar}] {gi+1}/{total_groups} 完成", flush=True)

    print(f"\r{' '*80}\r   ✅  医学术语去重: {fixed} 条已修复\n")

    # ── HPO ID 兜底：处理残留碰撞 ──
    residual = find_collisions(data)
    if residual:
        fixed2 = 0
        for shared_trans, hpo_ids in residual:
            print(f"\n   🔍 残留碰撞: \"{shared_trans[:60]}\" ({len(hpo_ids)} 个)")
            for hpo_id in hpo_ids:
                en_name = all_terms_en.get(hpo_id, "?")
                print(f"      {hpo_id}  EN: {en_name[:60]}")
                data[hpo_id] = f"{shared_trans}（{hpo_id}）"
                fixed2 += 1
        save_dict(output_path, data)
        print(f"\n   ✅  HPO ID 兜底: {fixed2} 条残留碰撞已修复\n")

    return data


# ----------------------------------------------------------------- Deterministic dedup（中文版 — 通俗说法）
def resolve_all_collisions_zh(
    data: Dict[str, str],
    hpo_names_zh: Dict[str, str],
    all_terms_en: Dict[str, str],
    output_path: str,
) -> Tuple[Dict[str, str], int]:
    """
    确定性去重——不使用 LLM。
    优先用中文 HPO 名称做后缀，没有则用英文原名。
    每个去重组保存后立即写盘（崩溃安全）。
    """
    collisions = find_collisions(data)
    if not collisions:
        return data, 0

    total_groups = len(collisions)
    total_dupes = sum(len(ids) - 1 for _, ids in collisions)
    print(f"\n🧹 中文去重: {total_groups} 组冲突, {total_dupes} 个重复")
    print(f"   策略: 优先使用中文 HPO 名做后缀区分，无则用英文名\n")

    fixed = 0
    for gi, (shared_trans, hpo_ids) in enumerate(collisions):
        bar = "█" * int((gi / max(total_groups, 1)) * 20) + "░" * 20
        bar = bar[:20]
        desc = (
            f"   [{bar}] {gi+1}/{total_groups} "
            f"'{shared_trans[:25]}' ({len(hpo_ids)} 个词)"
        )
        print(f"\r{desc:<80}", end="", flush=True)

        used_in_group: Set[str] = set()
        changes = 0
        for hpo_id in hpo_ids:
            # 优先用中文 HPO 名做后缀；但如果中文名已经带有英文后缀（即医学去重已处理过），
            # 直接用英文原名避免双括号嵌套。
            zh_name = hpo_names_zh.get(hpo_id, "")
            en_name = all_terms_en.get(hpo_id, hpo_id)
            if zh_name and "（" in zh_name:
                # 医学去重已处理 → 用英文原名做后缀，避免「（术语（英文））」的双重括号
                suffix = en_name
            else:
                suffix = zh_name or en_name
            candidate = f"{shared_trans}（{suffix}）"
            data[hpo_id] = candidate
            used_in_group.add(candidate)
            changes += 1

        save_dict(output_path, data)
        fixed += changes

        if gi % 50 == 0:
            print(
                f"\r{' '*80}\r   [{bar}] {gi+1}/{total_groups} 完成",
                flush=True,
            )

    print(f"\r{' '*80}\r   ✅  全部 {total_groups} 组完成: {fixed} 条已修复\n")
    return data, 1


# ----------------------------------------------------------------- Translation runner（并发 + 进度条）
def run_translation_phase(
    api_base: str,
    api_key: str,
    model: str,
    all_terms: Dict[str, str],
    existing: Dict[str, str],
    output_path: str,
    phase_label: str,
    system_prompt: str,
    few_shot: List[Tuple[str, str]],
    max_workers: int = 15,
    proxies=None,
) -> Dict[str, str]:
    """并发翻译 + 实时进度条 + 失败诊断。"""
    import concurrent.futures
    import threading

    remaining = {k: v for k, v in all_terms.items() if k not in existing}
    if not remaining:
        print(f"\n   ⏭  {phase_label}: 已全部完成，跳过翻译")
        return dict(existing)

    print(f"\n{'='*60}")
    print(f" {phase_label} — 翻译 ({model})")
    print(f"{'='*60}")
    print(f"🤖 待翻译: {len(remaining):,} 条 (线程数: {max_workers})\n")

    result = dict(existing)
    items = list(remaining.items())
    total = len(remaining)
    total_all = len(all_terms)

    # 共享计数器（线程安全）
    lock = threading.Lock()
    completed = [0]
    succeeded = [0]
    failed = [0]
    start_time = [time.time()]
    # 收集前几条失败原因用于诊断
    early_failures: List[str] = []

    def process_item(item):
        hpo_id, hpo_name = item
        t = translate_single(api_base, api_key, model, hpo_name, system_prompt, few_shot, proxies=proxies)
        return hpo_id, hpo_name, t

    def _progress_bar(n_done: int, n_ok: int, n_fail: int) -> str:
        pct = n_done / max(total, 1)
        bar_w = 30
        filled = int(bar_w * pct)
        bar = "█" * filled + "░" * (bar_w - filled)
        elapsed = time.time() - start_time[0]
        eta = (elapsed / max(n_done, 1)) * (total - n_done) if n_done > 0 else 0
        eta_str = f"{int(eta // 60)}m{int(eta % 60)}s" if eta < 3600 else f"{eta/60:.0f}m"
        return (
            f"\r   [{bar}] {pct*100:5.1f}% "
            f"({n_done:,}/{total:,}) "
            f"✅{n_ok} ❌{n_fail} "
            f"ETA {eta_str}"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_item, item): item for item in items}

        for future in concurrent.futures.as_completed(futures):
            hpo_id, hpo_name, t = future.result()
            with lock:
                completed[0] += 1
                if t:
                    result[hpo_id] = t
                    succeeded[0] += 1
                else:
                    failed[0] += 1
                    if len(early_failures) < 5:
                        early_failures.append(hpo_name[:60])

                # 每完成 1 条都刷新进度条
                print(_progress_bar(completed[0], succeeded[0], failed[0]),
                      end="", flush=True)

                # 定期保存
                if completed[0] % 200 == 0:
                    save_dict(output_path, result)

    save_dict(output_path, result)
    print()  # newline after progress bar

    # 失败诊断
    all_failed = succeeded[0] == 0
    if all_failed:
        print(f"\n❌  全部 {failed[0]:,} 条翻译失败！可能原因：")
        print(f"   1. API key 是否正确 export？")
        print(f"      export DEEPSEEK_API_KEY='sk-...'  ← 必须有 export")
        print(f"   2. 网络是否能访问 {api_base}？")
        print(f"   3. 检查代理设置 (Clash 7890)")
        if early_failures:
            print(f"   失败样本: {early_failures[:3]}")
    elif failed[0] > 0:
        fail_rate = failed[0] / max(completed[0], 1) * 100
        print(f"\n   ⚠  失败率: {fail_rate:.1f}% ({failed[0]}/{completed[0]})")
        if early_failures:
            print(f"   失败样本: {early_failures[:3]}")

    elapsed = time.time() - start_time[0]
    rate = completed[0] / max(elapsed, 1)
    print(f"   ⏱  耗时 {elapsed:.0f}s ({rate:.1f} 条/秒)")
    print(f"   ✅  {phase_label} 完成: {len(result):,} 条, 失败 {failed[0]} 条")
    return result


# ----------------------------------------------------------------- Main
def main():
    parser = argparse.ArgumentParser(
        description="中文版 HPO 术语翻译 + 通俗化映射 + 零碰撞去重"
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--mode",
        default="both",
        choices=["medical", "layperson", "both"],
        help="生成模式: medical=专业术语, layperson=通俗说法, both=全部",
    )
    parser.add_argument("--workers", type=int, default=15,
                       help="并发线程数 (默认 15)")
    parser.add_argument("--dry-run", action="store_true", help="估算费用不实际运行")
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="跳过翻译，仅对已有 layperson_dict_zh.json 去重",
    )
    args = parser.parse_args()

    all_terms_en = load_phenotype_terms(NODES_CSV)
    if not all_terms_en:
        print("❌  未找到表型术语", file=sys.stderr)
        sys.exit(1)

    # 估算费用
    passes = {"medical": 1, "layperson": 1, "both": 2}[args.mode]
    if args.dry_run:
        estimate_cost(len(all_terms_en), args.model, args.api_base, passes)
        # 检查已有字典
        if os.path.isfile(LAYERSON_ZH_JSON):
            existing = load_existing_dict(LAYERSON_ZH_JSON)
            c = find_collisions(existing)
            if c:
                dupes = sum(len(ids) for _, ids in c)
                print(f"   ⚠  layperson_dict_zh.json 有 {dupes} 个重复 — 用 --resolve-only 修复")
            else:
                print(f"   ✅  layperson_dict_zh.json: 零碰撞")
        return

    api_key = detect_api_key(args.api_base)
    provider = "DeepSeek" if "deepseek" in args.api_base else "OpenAI"
    proxies = detect_proxy()

    # ── 连接测试：裸 requests，打印完整诊断 ──
    import requests as _req
    ok = False
    try:
        r = _req.post(
            f"{args.api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": args.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3},
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
        print(f"\n❌ API 连接失败！诊断：")
        print(f"   API: {args.api_base}")
        print(f"   Key: {api_key[:12]}...  (长度 {len(api_key)})")
        print(f"   代理: {proxies or '无（直连）'}")
        print(f"   → 如在中国大陆，需 Clash 开启且端口 7890 可访问")
        sys.exit(1)
    print()

    hpo_names_zh: Dict[str, str] = {}
    layperson_zh: Dict[str, str] = {}

    # ═══════════════════════════════════════════════════
    # Phase 1a: 生成中文专业术语 (hpo_names_zh.json)
    # ═══════════════════════════════════════════════════
    if args.mode in ("medical", "both") and not args.resolve_only:
        existing_medical = load_existing_dict(HPO_NAMES_ZH_JSON)
        if existing_medical:
            print(f"📂 已有中文术语: {len(existing_medical):,} 条")

        hpo_names_zh = run_translation_phase(
            api_base=args.api_base,
            api_key=api_key,
            model=args.model,
            all_terms=all_terms_en,
            existing=existing_medical,
            output_path=HPO_NAMES_ZH_JSON,
            phase_label="Phase 1a — 中文专业术语",
            system_prompt=MEDICAL_SYSTEM_PROMPT,
            few_shot=MEDICAL_FEW_SHOT,
            max_workers=args.workers,
            proxies=proxies,
        )

        # Phase 1a.5: 医学术语去重 — 必须在 layperson Phase 之前完成
        # 否则 Phase 2 用到的中文后缀可能不唯一
        medical_collisions = find_collisions(hpo_names_zh)
        if medical_collisions:
            dupes = sum(len(ids) for _, ids in medical_collisions)
            print(f"\n{'='*60}")
            print(f" Phase 1a.5 — 医学术语去重")
            print(f"{'='*60}")
            print(f"🔍 {dupes} 个重复分布在 {len(medical_collisions)} 组")
            print(f"   ⚠  必须在构建通俗说法前消除 → 否则 Phase 2 后缀来源不唯一")
            hpo_names_zh = resolve_medical_collisions_zh(
                hpo_names_zh, all_terms_en, HPO_NAMES_ZH_JSON
            )
            save_dict(HPO_NAMES_ZH_JSON, hpo_names_zh)
            uniq = len(set(hpo_names_zh.values()))
            if uniq == len(hpo_names_zh):
                print(f"   ✅  医学术语零碰撞完成")
            else:
                print(f"   ⚠  仍有 {len(hpo_names_zh) - uniq} 重复（异常，请检查）")
        else:
            print(f"\n   ✅  中文专业术语: 零碰撞，无需去重")

    # ═══════════════════════════════════════════════════
    # Phase 1b: 生成中文通俗说法 (layperson_dict_zh.json)
    # ═══════════════════════════════════════════════════
    if args.mode in ("layperson", "both") and not args.resolve_only:
        existing_layperson = load_existing_dict(LAYERSON_ZH_JSON)
        if existing_layperson:
            print(f"📂 已有通俗说法: {len(existing_layperson):,} 条")

        layperson_zh = run_translation_phase(
            api_base=args.api_base,
            api_key=api_key,
            model=args.model,
            all_terms=all_terms_en,
            existing=existing_layperson,
            output_path=LAYERSON_ZH_JSON,
            phase_label="Phase 1b — 中文通俗说法",
            system_prompt=LAYPERSON_SYSTEM_PROMPT,
            few_shot=LAYPERSON_FEW_SHOT,
            max_workers=args.workers,
            proxies=proxies,
        )

    # ═══════════════════════════════════════════════════
    # Phase 2: 去重（仅 layperson 字典需要零碰撞）
    # ═══════════════════════════════════════════════════
    if args.mode in ("layperson", "both") or args.resolve_only:
        if not layperson_zh:
            layperson_zh = load_existing_dict(LAYERSON_ZH_JSON)

        if not hpo_names_zh:
            hpo_names_zh = load_existing_dict(HPO_NAMES_ZH_JSON)

        collisions_before = find_collisions(layperson_zh)
        if collisions_before:
            print(f"\n{'='*60}")
            print(f" Phase 2 — 确定性去重（中文）")
            print(f"{'='*60}")
            print(
                f"🔍 {sum(len(ids) for _, ids in collisions_before)} 个重复 "
                f"分布在 {len(collisions_before)} 组"
            )
            layperson_zh, _ = resolve_all_collisions_zh(
                layperson_zh, hpo_names_zh, all_terms_en, LAYERSON_ZH_JSON
            )
            save_dict(LAYERSON_ZH_JSON, layperson_zh)
        else:
            print(f"\n🎉  零碰撞！")

    # ═══════════════════════════════════════════════════
    # 最终统计
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f" 📊 最终统计")
    print(f"{'='*60}")

    if hpo_names_zh:
        uniq = len(set(hpo_names_zh.values()))
        if uniq == len(hpo_names_zh):
            print(f"   ✅  中文专业术语: {len(hpo_names_zh):,} 条 — 零碰撞")
        else:
            print(f"   ⚠  中文专业术语: {len(hpo_names_zh):,} 条 — {len(hpo_names_zh) - uniq} 重复残留")
        print(f"   输出: {HPO_NAMES_ZH_JSON}")

    if layperson_zh:
        uniq = len(set(layperson_zh.values()))
        if uniq == len(layperson_zh):
            print(f"   🎉  中文通俗说法: {len(layperson_zh):,} 条 — 零碰撞！")
        else:
            print(f"   ⚠  中文通俗说法: {len(layperson_zh):,} 条 — {len(layperson_zh) - uniq} 重复残留")
        print(f"   输出: {LAYERSON_ZH_JSON}")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
