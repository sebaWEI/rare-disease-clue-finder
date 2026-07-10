import { useI18n } from "../../i18n/i18n";
import type { SmartSearchResponse } from "../../types";
import styles from "./intake.module.css";

interface SmartMatchGroupsProps {
  data: SmartSearchResponse;
  isSelected: (id: string) => boolean;
  onToggle: (id: string, name: string) => void;
}

export function SmartMatchGroups({ data, isSelected, onToggle }: SmartMatchGroupsProps) {
  const { t } = useI18n();
  const total = data.groups.reduce((sum, g) => sum + (g.results?.length || 0), 0);
  if (total === 0) return null;

  const label =
    data.fragments.length > 1
      ? `${t("suggested_matches")} · ${data.fragments.length}`
      : t("suggested_matches");

  return (
    <div className={styles.matches}>
      <div className={styles.matchesLabel}>{label}</div>
      {data.groups.map((group) => {
        if (!group.results || group.results.length === 0) return null;
        return (
          <div key={group.fragment} className={styles.group}>
            <span className={styles.groupLabel}>{group.fragment}</span>
            <div className={styles.bubbles}>
              {group.results.map((r, i) => {
                const selected = isSelected(r.hpo_id);
                const isLab = r.score >= 1.49;
                const cls = [
                  styles.bubble,
                  isLab ? styles.bubbleLab : "",
                  selected ? styles.bubbleSelected : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <button
                    key={r.hpo_id}
                    type="button"
                    className={cls}
                    style={{ animation: `rd-fade-in 0.35s ${i * 0.05}s var(--ease) both` }}
                    onClick={() => onToggle(r.hpo_id, r.name)}
                    title={isLab ? "lab marker" : `score ${r.score.toFixed(2)}`}
                  >
                    <span className={styles.bubblePlus}>{selected ? "✓" : "+"}</span>
                    {r.name}
                    {r.hint && <span className={styles.bubbleHint}>[{r.hint}]</span>}
                    {isLab && <span className={styles.bubbleTag}>lab</span>}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
