import { useI18n } from "../../i18n/i18n";
import type { HpoItem } from "../../hooks/useHpoSelection";
import styles from "./intake.module.css";

interface SelectedChipsProps {
  items: HpoItem[];
  onRemove: (id: string) => void;
}

export function SelectedChips({ items, onRemove }: SelectedChipsProps) {
  const { t } = useI18n();
  return (
    <div className={styles.selected}>
      <div className={styles.selectedHead}>{t("selected_symptoms")}</div>
      <div className={styles.chips}>
        {items.length === 0 ? (
          <span className={styles.chipEmpty}>{t("tap_to_add")}</span>
        ) : (
          items.map((it) => (
            <span key={it.id} className={styles.chip}>
              {it.name}
              <button
                type="button"
                className={styles.chipRemove}
                aria-label={`Remove ${it.name}`}
                onClick={() => onRemove(it.id)}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>
    </div>
  );
}
