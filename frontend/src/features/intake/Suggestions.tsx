import { useI18n } from "../../i18n/i18n";
import type { SuggestedHpo } from "../../types";
import styles from "./intake.module.css";

interface SuggestionsProps {
  suggestions: SuggestedHpo[];
  isSelected: (id: string) => boolean;
  onToggle: (id: string, name: string) => void;
}

export function Suggestions({ suggestions, isSelected, onToggle }: SuggestionsProps) {
  const { t } = useI18n();
  if (!suggestions || suggestions.length === 0) return null;
  return (
    <div className={styles.suggestions}>
      <div className={styles.matchesLabel}>{t("also_experiencing")}</div>
      <div className={styles.bubbles}>
        {suggestions.map((s, i) => {
          const selected = isSelected(s.hpo_id);
          return (
            <button
              key={s.hpo_id}
              type="button"
              className={`${styles.suggestBubble} ${selected ? styles.bubbleSelected : ""}`}
              style={{ animation: `rd-fade-in 0.35s ${i * 0.05}s var(--ease) both` }}
              onClick={() => onToggle(s.hpo_id, s.name)}
              title={s.reason}
            >
              <span className={styles.bubblePlus}>{selected ? "✓" : "+"}</span>
              {s.name}
              {s.reason && <span className={styles.suggestReason}>— {s.reason}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
