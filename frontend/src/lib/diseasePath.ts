/** Build /disease/... path; encode so ORPHA:280 survives routing. */
export function diseasePath(diseaseId: string): string {
  return `/disease/${encodeURIComponent(diseaseId)}`;
}

export function expertPath(diseaseId: string, expertId: string): string {
  return `${diseasePath(diseaseId)}/expert/${encodeURIComponent(expertId)}`;
}
