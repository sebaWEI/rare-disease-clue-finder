// Typed API layer. Every call is same-origin (relative /api paths); in dev the
// Vite proxy forwards to the FastAPI backend on :8000, in production FastAPI
// serves both the built assets and the API from the same origin.

import type {
  DescribeMissingPrediction,
  DescribeMissingResponse,
  Lang,
  PredictResponse,
  SmartSearchResponse,
} from "../types";

// All patient-facing calls run against the public mode of the backend.
const MODE = "public";

async function getJSON<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail || `Server error (${resp.status})`);
  }
  return (await resp.json()) as T;
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail || `Server error (${resp.status})`);
  }
  return (await resp.json()) as T;
}

/** Zero-click free-text diagnosis: narrative in, ranked disease clues out. */
export function autoDiagnose(text: string, lang: Lang): Promise<PredictResponse> {
  const url = `/api/auto-diagnose?text=${encodeURIComponent(text)}&mode=${MODE}&lang=${lang}`;
  return getJSON<PredictResponse>(url);
}

/** Free-text to grouped HPO candidates for manual selection. */
export function smartSearch(text: string, lang: Lang): Promise<SmartSearchResponse> {
  const url = `/api/smart-search?text=${encodeURIComponent(text)}&mode=${MODE}&lang=${lang}`;
  return getJSON<SmartSearchResponse>(url);
}

/** True Path Rule inference over a set of selected HPO terms. */
export function predict(hpoIds: string[], lang: Lang): Promise<PredictResponse> {
  const url = `/api/predict?mode=${MODE}&lang=${lang}`;
  return postJSON<PredictResponse>(url, { hpo_ids: hpoIds });
}

/** Layperson descriptions of missing key symptoms for self-check. */
export function describeMissing(
  originalText: string,
  predictions: DescribeMissingPrediction[],
  lang: Lang,
): Promise<DescribeMissingResponse> {
  return postJSON<DescribeMissingResponse>("/api/describe-missing", {
    original_text: originalText,
    predictions,
    lang,
  });
}
