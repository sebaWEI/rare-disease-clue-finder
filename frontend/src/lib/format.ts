// Small presentation helpers shared across report and result views.

export function pct(ratio: number): number {
  return Math.round((ratio || 0) * 100);
}

/** Color for a coverage / explained-ratio value (0..1). */
export function coverageColor(ratio: number): string {
  const r = ratio || 0;
  if (r >= 0.6) return "var(--ok)";
  if (r >= 0.3) return "var(--warn)";
  return "var(--danger)";
}

export function truncate(str: string, maxLen: number): string {
  return str.length > maxLen ? str.slice(0, maxLen - 1) + "…" : str;
}

export function formatTimestamp(lang: "en" | "zh"): string {
  return new Date().toLocaleString(lang === "zh" ? "zh-CN" : "en-GB", {
    dateStyle: "long",
    timeStyle: "short",
  });
}
