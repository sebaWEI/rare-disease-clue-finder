import { useI18n } from "../../i18n/i18n";
import type { HpoItem } from "../../hooks/useHpoSelection";
import styles from "./report.module.css";

export function SymptomSummary({ symptoms }: { symptoms: HpoItem[] }) {
  const { t } = useI18n();
  if (symptoms.length === 0) return null;
  return (
    <div className={styles.panel}>
      <div className={styles.sectionTitle}>
        <span className={styles.sectionNum}>01</span>
        {t("section_symptoms")}
      </div>
      <table className={styles.symptoms}>
        <thead>
          <tr>
            <th>{t("col_num")}</th>
            <th>{t("col_symptom")}</th>
          </tr>
        </thead>
        <tbody>
          {symptoms.map((s, i) => (
            <tr key={s.id}>
              <td className={styles.symIndex}>{i + 1}</td>
              <td style={{ fontWeight: 500 }}>{s.name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
