// Decorative DNA double-helix, generated from a sine so it stays crisp at any
// size. Purely ornamental — hidden from assistive tech.

interface HelixProps {
  className?: string;
}

const TURNS = 5;
const SEGMENTS = 130;
const WIDTH = 120;
const HEIGHT = 620;
const AMP = 34;
const MID = WIDTH / 2;

function strandPath(phase: number): string {
  const pts: string[] = [];
  for (let i = 0; i <= SEGMENTS; i++) {
    const t = i / SEGMENTS;
    const y = t * HEIGHT;
    const x = MID + Math.sin(t * Math.PI * 2 * TURNS + phase) * AMP;
    pts.push(`${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return pts.join(" ");
}

export function Helix({ className }: HelixProps) {
  const rungs = Array.from({ length: TURNS * 2 + 1 }, (_, k) => {
    const t = k / (TURNS * 2);
    const y = t * HEIGHT;
    const x1 = MID + Math.sin(t * Math.PI * 2 * TURNS) * AMP;
    const x2 = MID + Math.sin(t * Math.PI * 2 * TURNS + Math.PI) * AMP;
    const near = Math.abs(x1 - x2) < AMP * 0.9;
    return { y, x1, x2, near };
  });

  return (
    <svg
      className={className}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      fill="none"
      aria-hidden="true"
      preserveAspectRatio="xMidYMid slice"
    >
      <path d={strandPath(0)} stroke="rgba(233,245,236,0.85)" strokeWidth="2.4" strokeLinecap="round" />
      <path d={strandPath(Math.PI)} stroke="rgba(184,217,46,0.85)" strokeWidth="2.4" strokeLinecap="round" />
      {rungs.map((r, i) => (
        <line
          key={i}
          x1={r.x1}
          y1={r.y}
          x2={r.x2}
          y2={r.y}
          stroke={r.near ? "rgba(233,245,236,0.55)" : "rgba(233,245,236,0.2)"}
          strokeWidth="1.6"
        />
      ))}
    </svg>
  );
}
