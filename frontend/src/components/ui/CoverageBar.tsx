import { coverageColor, pct } from "../../lib/format";
import styles from "./ui.module.css";

interface CoverageBarProps {
  ratio: number;
  label: string;
}

export function CoverageBar({ ratio, label }: CoverageBarProps) {
  const value = pct(ratio);
  const color = coverageColor(ratio);
  return (
    <div className={styles.coverage}>
      <div className={styles.coverageHead}>
        <span className={styles.coverageLabel}>{label}</span>
        <span className={styles.coverageValue} style={{ color }}>
          {value}%
        </span>
      </div>
      <div className={styles.coverageTrack}>
        <div className={styles.coverageFill} style={{ width: `${value}%`, background: color }} />
      </div>
    </div>
  );
}
