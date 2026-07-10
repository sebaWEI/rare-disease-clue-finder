import { useI18n } from "../../i18n/i18n";
import { CoverageBar } from "../../components/ui/CoverageBar";
import type { DiseaseResult } from "../../types";
import styles from "./report.module.css";

const RANK_CLASS = [styles.rank1, styles.rank2, styles.rank3];

interface ClueCardProps {
  disease: DiseaseResult;
  index: number;
}

export function ClueCard({ disease, index }: ClueCardProps) {
  const { t } = useI18n();
  const paths = disease.matched_paths || [];
  const missing = disease.missing_critical_hpos || [];
  const rankClass = RANK_CLASS[index] || styles.rankOther;

  return (
    <div
      className={styles.clue}
      data-disease-id={disease.disease_id}
      style={{ animationDelay: `${index * 0.08}s` }}
    >
      <div className={styles.clueHead}>
        <div className={styles.clueHeadLeft}>
          <div className={`${styles.rank} ${rankClass}`}>{index + 1}</div>
          <div style={{ minWidth: 0 }}>
            <div className={styles.clueId}>{disease.disease_id}</div>
            <div className={styles.clueName}>{disease.disease_name}</div>
          </div>
        </div>
        <div className={styles.clueCoverage}>
          <CoverageBar ratio={disease.explained_ratio} label={t("coverage")} />
        </div>
      </div>

      <div className={styles.clueBody}>
        <div className={styles.evLabel}>
          {t("evidence_paths")} · {paths.length}
        </div>
        {paths.map((p, i) => {
          const arrow = p.match_type === "direct" ? "→" : p.match_type === "child" ? "↘" : "↗";
          return (
            <div className={styles.evRow} key={`${p.input_hpo_id}-${p.matched_hpo_id}-${i}`}>
              <span className={styles.evArrow}>{arrow}</span>
              <span className={styles.evText}>
                <strong>{p.input_hpo_name}</strong>{" "}
                {p.match_type === "direct" ? t("is_associated") : t("matched_via")}{" "}
                <strong>{p.matched_hpo_name}</strong>
              </span>
            </div>
          );
        })}
      </div>

      {missing.length > 0 && (
        <div className={styles.clueMissing}>
          <div className={styles.clueMissingHead}>{t("critical_missing")}</div>
          {missing.map((m) => (
            <div key={m.hpo_id} className={styles.clueMissingItem}>
              {m.name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
