import { useI18n } from "../../i18n/i18n";
import styles from "./layout.module.css";
import type { StringKey } from "../../i18n/strings";

const ITEMS: StringKey[] = ["evidence_led", "explainable", "privacy_local"];

function Tick() {
  return (
    <svg className={styles.assuranceTick} width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
      <path d="M2 6.4 4.6 9 10 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function AssuranceBar() {
  const { t } = useI18n();
  return (
    <div className={styles.assurance}>
      <div className={styles.assuranceInner}>
        {ITEMS.map((key, i) => (
          <span key={key} style={{ display: "inline-flex", alignItems: "center", gap: "10px" }}>
            {i > 0 && <span className={styles.assuranceSep} aria-hidden />}
            <span className={styles.assuranceItem}>
              <Tick />
              {t(key)}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
