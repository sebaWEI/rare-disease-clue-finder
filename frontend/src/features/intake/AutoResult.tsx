import { Link } from "react-router-dom";
import { useI18n } from "../../i18n/i18n";
import { CoverageBar } from "../../components/ui/CoverageBar";
import { Button } from "../../components/ui/Button";
import { coverageColor, pct } from "../../lib/format";
import { diseasePath } from "../../lib/diseasePath";
import type { PredictResponse } from "../../types";
import styles from "./intake.module.css";

interface AutoResultProps {
  data: PredictResponse;
  onViewReport: () => void;
}

export function AutoResult({ data, onViewReport }: AutoResultProps) {
  const { t } = useI18n();
  const selections = data.auto_selections || [];
  const results = data.results || [];

  return (
    <div className={styles.autoWrap}>
      {selections.length > 0 && (
        <div className={styles.mapping}>
          <div className={styles.mappingHead}>{t("symptom_mapping")}</div>
          <div>
            {selections.map((s, i) => {
              const matched = s.match_method !== "none" && s.matched_hpo_name;
              return (
                <span key={i} className={styles.mapItem}>
                  <span className={styles.mapRaw}>{s.raw_description}</span>
                  <span className={styles.mapArrow}>→</span>
                  <span className={matched ? styles.mapTerm : styles.mapNone}>
                    {matched ? s.matched_hpo_name : t("no_match_short")}
                  </span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {results.length === 0 ? (
        <div className={styles.noMatch}>{t("no_match")}</div>
      ) : (
        <>
          <TopMatch data={data} />
          {results.length > 1 && (
            <div className={styles.others}>
              <div className={styles.othersHead}>{t("other_candidates")}</div>
              {results.slice(1).map((d) => (
                <div key={d.disease_id} className={styles.otherRow}>
                  <Link className={styles.otherNameLink} to={diseasePath(d.disease_id)}>
                    {d.disease_name}
                  </Link>
                  <span className={styles.otherMeta}>
                    <span style={{ color: coverageColor(d.explained_ratio) }}>
                      {pct(d.explained_ratio)}%
                    </span>
                    <span style={{ color: "var(--ink-mute)" }}>{d.total_score.toFixed(2)}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          <div>
            <Button variant="brand" onClick={onViewReport}>
              {t("btn_view_report")} →
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function TopMatch({ data }: { data: PredictResponse }) {
  const { t } = useI18n();
  const top = data.results[0];
  const missing = top.missing_critical_hpos || [];
  return (
    <div className={styles.topMatch}>
      <div className={styles.topKicker}>{t("top_match")}</div>
      <Link className={styles.topNameLink} to={diseasePath(top.disease_id)}>
        {top.disease_name}
      </Link>
      <div className={styles.topId}>{top.disease_id}</div>
      <div className={styles.topMeta}>
        <div className={styles.topStat}>
          <div className={styles.topStatValue}>{(top.matched_paths || []).length}</div>
          <div className={styles.topStatLabel}>{t("evidence_paths_count")}</div>
        </div>
        <div className={`${styles.topStat} ${styles.topStatGrow}`}>
          <CoverageBar ratio={top.explained_ratio} label={t("coverage")} />
        </div>
      </div>
      {missing.length > 0 && (
        <div className={styles.missing}>
          <div className={styles.missingHead}>{t("critical_missing")}</div>
          <div style={{ fontSize: "0.78rem", color: "var(--warn)", marginBottom: 4 }}>
            {t("triage_prompt")}
          </div>
          {missing.map((m) => (
            <div key={m.hpo_id} className={styles.missingItem}>
              {m.name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
