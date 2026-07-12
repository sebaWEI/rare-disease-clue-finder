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

  // ── Care guide ──
  guide_kicker: ["Care guide", "就医指南"],
  guide_back: ["Back to clue finder", "返回线索发现"],
  guide_loading: ["Loading care guide…", "正在加载就医指南……"],
  guide_err: ["Failed to load care guide.", "就医指南加载失败。"],
  guide_care_title: ["Where to start", "就医导览"],
  guide_care_desc: [
    "Prefer centres with rare-disease clinics, genetics, developmental paediatrics, paediatric neurology, or rehab.",
    "优先考虑设有罕见病门诊、遗传代谢、发育儿科、儿童神经或康复的医疗机构。",
  ],
  guide_experts_title: ["Specialists on the map", "地图就医导航"],
  guide_experts_desc: [
    "Pick a highlighted province or locate yourself. Specialists fan out from the selected region — confirm via official booking channels.",
    "点选高亮省份，或一键定位。选中省份后右侧半轮盘展示当地专家——实际就诊请以医院官方预约渠道为准。",
  ],
  guide_placeholder: [
    "A curated care guide for this disease is still being prepared. You can check Orphanet in the meantime.",
    "该病的就医指南仍在建设中。您可先查阅 Orphanet。",
  ],
  guide_city: ["City / province", "省市"],
  guide_locate: ["Use my location", "使用我的定位"],
  guide_locating: ["Locating…", "定位中……"],
  guide_located: ["Sorted by your location (approx.).", "已按您的定位排序（估算）。"],
  guide_city_hint: [
    "Location unavailable — pick a city above to approximate nearest centres.",
    "无法获取定位——请上方选择省市以估算就近医院。",
  ],
  guide_logo_soon: ["logo", "院徽"],
  guide_expand: ["Show more", "展开"],
  guide_collapse: ["Show less", "收起"],
  guide_type_doctor: ["Doctor", "医生"],
  guide_type_team: ["Team", "团队"],
  guide_type_dept: ["Department", "科室"],
  guide_open: ["Open care guide", "查看就医指南"],
  map_amap: ["Amap", "高德地图"],
  map_baidu: ["Baidu Maps", "百度地图"],
  map_google: ["Google Maps", "Google 地图"],
  map_apple: ["Apple Maps", "Apple 地图"],
  map_aria: ["China map of specialist provinces", "专家省份中国地图"],
  map_hint_pick: [
    "Click a green province, use the chips below, or locate yourself.",
    "点击绿色省份、下方标签，或一键定位。",
  ],
  map_hint_wheel: [
    "Scroll to browse · click the center card for details.",
    "滚轮浏览精简条目 · 点击中间条目进入详情。",
  ],
  map_open_detail: ["Details", "详情"],
  map_locate: ["Locate nearest province", "一键定位"],
  map_back_china: ["Back to China map", "返回全国地图"],
  map_no_experts: ["No curated specialists listed here yet.", "暂无收录专家"],
  map_expert_count: ["{n} specialist(s)", "{n} 位专家 / 团队"],
  map_located_province: ["Nearest curated region: {p}", "最近收录地区：{p}"],
  expert_kicker: ["Specialist", "专家详情"],
  expert_back: ["Back to map", "返回地图"],
  expert_not_found: ["This specialist entry was not found.", "未找到该专家条目。"],
  expert_advantage: ["Hospital strength", "医院优势"],
  expert_bio: ["Profile", "简介"],
  expert_maps: ["Open in maps", "打开地图"],
} as const;

export type StringKey = keyof typeof STRINGS;

export function translate(key: StringKey, lang: Lang): string {
  const entry = STRINGS[key];
  return entry ? entry[lang === "zh" ? 1 : 0] : (key as string);
}
