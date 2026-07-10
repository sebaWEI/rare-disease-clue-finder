import type { HpoItem } from "../hooks/useHpoSelection";
import type { PredictResponse } from "../types";

// Resolve the ordered list of symptoms to show in the report. Names can come
// from the manual selection, the auto-diagnose mapping, or the matched paths of
// the results — we merge all known names and order them by query_hpo_ids.
export function resolveReportSymptoms(
  data: PredictResponse,
  selection: HpoItem[],
): HpoItem[] {
  const names = new Map<string, string>();
  for (const it of selection) names.set(it.id, it.name);
  for (const sel of data.auto_selections || []) {
    if (sel.matched_hpo_id && sel.matched_hpo_name) {
      names.set(sel.matched_hpo_id, sel.matched_hpo_name);
    }
  }
  for (const disease of data.results || []) {
    for (const p of disease.matched_paths || []) {
      if (p.input_hpo_id && p.input_hpo_name && !names.has(p.input_hpo_id)) {
        names.set(p.input_hpo_id, p.input_hpo_name);
      }
    }
  }

  const order = data.query_hpo_ids || [];
  if (order.length > 0) {
    return order
      .filter((id) => names.has(id))
      .map((id) => ({ id, name: names.get(id)! }));
  }
  if (selection.length > 0) return selection;
  return Array.from(names.entries()).map(([id, name]) => ({ id, name }));
}

/** HPO items derived from an auto-diagnose response, for "back to edit". */
export function autoSelectionItems(data: PredictResponse | null): HpoItem[] {
  if (!data?.auto_selections) return [];
  const items: HpoItem[] = [];
  for (const sel of data.auto_selections) {
    if (sel.matched_hpo_id) {
      items.push({ id: sel.matched_hpo_id, name: sel.matched_hpo_name || sel.matched_hpo_id });
    }
  }
  return items;
}
