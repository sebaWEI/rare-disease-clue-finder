// Brand monogram: a stylized magnifier over a genomic base — "finding clues in
// the genome". Small, crisp, works on the dark header block.

export function Mark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="14" cy="14" r="9" stroke="var(--brand-ink)" strokeWidth="2.2" />
      <line
        x1="20.5"
        y1="20.5"
        x2="27"
        y2="27"
        stroke="var(--spark)"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <path
        d="M10 16.5c2-2.6 4-2.6 6 0"
        stroke="var(--spark)"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <path
        d="M10 11.5c2 2.6 4 2.6 6 0"
        stroke="var(--brand-ink)"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="10" cy="16.5" r="1.1" fill="var(--spark)" />
      <circle cx="16" cy="11.5" r="1.1" fill="var(--brand-ink)" />
    </svg>
  );
}
