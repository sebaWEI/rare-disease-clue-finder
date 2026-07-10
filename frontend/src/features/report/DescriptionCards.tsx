import { useEffect, useState } from "react";
import { useI18n } from "../../i18n/i18n";
import { describeMissing } from "../../api/client";
import type { DescriptionItem, PredictResponse } from "../../types";
import styles from "./report.module.css";

interface DescriptionCardsProps {
  data: PredictResponse;
  originalText: string;
}

export function DescriptionCards({ data, originalText }: DescriptionCardsProps) {
  const { t, lang } = useI18n();
  const [items, setItems] = useState<DescriptionItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const results = data.results || [];
    if (results.length === 0) {
      setLoading(false);
      return;
    }
    const predictions = results.map((r) => ({
      disease_id: r.disease_id,
      disease_name: r.disease_name,
      matched_hpo_names: (r.matched_paths || []).map(
        (p) => p.matched_hpo_name || p.input_hpo_name,
      ),
      missing_hpo_names: (r.missing_critical_hpos || []).map((m) => m.name),
      explained_ratio: r.explained_ratio || 0,
    }));

    setLoading(true);
    setFailed(false);
    describeMissing(originalText, predictions, lang)
      .then((resp) => {
        if (cancelled) return;
        setItems(
          (resp.descriptions || []).filter((d) => {
            const desc = (d.description || "").trim();
            return desc !== "" && desc !== "-";
          }),
        );
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [data, originalText, lang]);

  function onSelect(item: DescriptionItem) {
    setSelected(item.disease_id);
    const el = document.querySelector<HTMLElement>(`[data-disease-id="${item.disease_id}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add(styles.clueSpot);
      window.setTimeout(() => el.classList.remove(styles.clueSpot), 1500);
    }
  }

  if (failed) return null;
  if (!loading && (!items || items.length === 0)) return null;

  return (
    <div className={styles.panel}>
      <div className={styles.descHead}>{t("desc_cards_title")}</div>
      {loading ? (
        <div className={styles.descLoading}>{t("desc_loading")}</div>
      ) : (
        <div className={styles.descGrid}>
          {items!.map((item, i) => (
            <button
              type="button"
              key={item.disease_id}
              className={`${styles.descCard} ${
                selected === item.disease_id ? styles.descCardActive : ""
              }`}
              style={{ animationDelay: `${i * 0.08}s` }}
              onClick={() => onSelect(item)}
            >
              <div className={styles.descCardHead}>
                <span className={styles.descRank}>{i + 1}</span>
                <span className={styles.descName}>{item.disease_name}</span>
              </div>
              <div className={styles.descText}>{item.description}</div>
              <span className={styles.descCheck}>✓</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
