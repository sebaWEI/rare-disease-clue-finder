import { useI18n } from "../../i18n/i18n";
import styles from "./report.module.css";

export function Disclaimer() {
  const { t } = useI18n();
  return (
    <div className={styles.disclaimer}>
      <svg
        className={styles.disclaimerIcon}
        width="18"
        height="18"
        viewBox="0 0 18 18"
        fill="none"
        aria-hidden
      >
        <path
          d="M9 1.5 17 16H1L9 1.5Z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path d="M9 6.5v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        <circle cx="9" cy="13" r="0.9" fill="currentColor" />
      </svg>
      <span>{t("disclaimer")}</span>
    </div>
  );
}
