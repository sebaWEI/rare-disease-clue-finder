import type { Lang } from "../types";

// [English, 中文] tuples. Keeping copy in one dictionary keeps the bilingual
// patient experience in lock-step and makes adding languages a small change.
export const STRINGS = {
  // ── Brand ──
  brand_name: ["Rare Disease Pre-diagnosis Clue Finder", "罕见病预诊断线索发现系统"],
  brand_tagline: [
    "HPO × Orphanet · Explainable True-Path Inference",
    "HPO × Orphanet · 可解释真路径推理",
  ],
  brand_kicker: ["iGEM 2026 · PekingHSC", "iGEM 2026 · PekingHSC"],

  // ── Assurance ──
  evidence_led: ["Evidence-led · HPO + Orphanet", "循证驱动 · HPO + Orphanet"],
  explainable: ["Explainable inference paths", "可解释推理路径"],
  privacy_local: ["Privacy-respecting local session", "隐私保护 · 本地会话"],

  // ── Input ──
  step_kicker: ["Intake / 01", "问诊 / 01"],
  intake_title: ["Describe what you feel", "描述您的感受"],
  intake_desc: [
    "Write your symptoms in your own words. We map them to standardized medical terms and surface possible rare-disease clues — with the reasoning shown, not hidden.",
    "用您自己的话写下症状。我们会将其映射到标准医学术语，并给出可能的罕见病线索——推理过程公开透明，而非黑箱。",
  ],
  narrative_label: ["Your description", "您的描述"],
  smart_placeholder: [
    "e.g. progressive muscle weakness, calf enlargement, frequent falls, tiptoe walking…",
    "例如：进行性肌无力、小腿变粗、频繁跌倒、踮脚走路……",
  ],

  // ── Mode toggle (auto vs manual) ──
  mode_auto: ["Auto clue-finding", "自动寻找线索"],
  mode_manual: ["Pick symptoms myself", "自己挑选症状"],
  mode_auto_hint: [
    "Describe freely — we do the rest.",
    "自由描述——其余交给我们。",
  ],
  mode_manual_hint: [
    "We suggest matching terms; you choose.",
    "我们建议匹配术语，由您选择。",
  ],

  // ── Buttons ──
  btn_auto: ["Find clues", "寻找线索"],
  btn_match: ["Match my symptoms", "匹配我的症状"],
  btn_analysing: ["Analysing…", "分析中……"],
  btn_run: ["See my clues", "查看我的线索"],
  btn_view_report: ["View full report", "查看完整报告"],
  btn_new: ["New search", "重新开始"],
  btn_edit: ["Back to edit", "返回修改"],
  btn_pdf: ["Export PDF", "导出 PDF"],
  btn_generating: ["Generating…", "生成中……"],

  // ── Smart match ──
  suggested_matches: ["Suggested matches — tap to add", "建议匹配——点击添加"],
  selected_symptoms: ["Your selected symptoms", "已选症状"],
  tap_to_add: ["Tap suggested terms to add them here.", "点击上方建议术语，添加到此处。"],
  also_experiencing: ["Do you also have any of these?", "您是否还有以下症状？"],

  // ── Auto results ──
  symptom_mapping: ["How we read your description", "我们如何理解您的描述"],
  top_match: ["Most likely clue", "最可能的线索"],
  other_candidates: ["Other possible clues", "其他可能的线索"],
  coverage: ["Symptom coverage", "症状覆盖度"],
  evidence_paths_count: ["evidence links", "条证据关联"],
  critical_missing: ["Key symptoms worth checking", "值得留意的关键症状"],
  triage_prompt: ["People with this often also have:", "该病常还伴有："],
  no_match: [
    "No clear disease clue matched your description. Try adding more detail, or switch to “Pick symptoms myself”.",
    "未能找到明确的疾病线索。请尝试补充更多细节，或切换到“自己挑选症状”。",
  ],
  no_match_short: ["(no match)", "（无匹配）"],

  // ── Report ──
  report_kicker: ["Clue report", "线索报告"],
  report_title: ["Your rare-disease clues", "您的罕见病线索"],
  disclaimer: [
    "This is a research prototype for iGEM 2026. Scores show statistical association strength — not a diagnosis or clinical probability. Always consult a qualified medical professional.",
    "本工具为 iGEM 2026 研究原型。评分表示统计关联强度——并非诊断或临床概率。请务必咨询专业医生。",
  ],
  section_symptoms: ["Symptoms we used", "我们使用的症状"],
  section_clues: ["Possible disease clues", "可能的疾病线索"],
  section_clues_desc: [
    "Ranked by True Path Rule inference over HPO + Orphanet. Association strength — not clinical probability.",
    "由 True Path Rule 在 HPO + Orphanet 上推理排序。表示关联强度——并非临床概率。",
  ],
  col_num: ["#", "#"],
  col_symptom: ["Symptom", "症状"],
  evidence_paths: ["Why this clue", "为何是此线索"],
  is_associated: ["is linked to", "关联到"],
  matched_via: ["points to", "指向"],
  report_generated: ["Report generated", "报告生成于"],

  // ── Desc cards ──
  desc_cards_title: ["Which description fits you best?", "以下哪种描述最符合您？"],
  desc_loading: ["preparing descriptions…", "正在生成描述……"],

  // ── Errors ──
  err_min_chars: ["Please describe at least a few words.", "请至少描述几个字。"],
  err_auto: ["Clue-finding failed", "寻找线索失败"],
  err_match: ["Symptom matching failed", "症状匹配失败"],
  err_pdf: ["PDF export failed.", "PDF 导出失败。"],
} as const;

export type StringKey = keyof typeof STRINGS;

export function translate(key: StringKey, lang: Lang): string {
  const entry = STRINGS[key];
  return entry ? entry[lang === "zh" ? 1 : 0] : (key as string);
}
