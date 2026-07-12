import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchGuide } from "../../api/client";
import { Kicker } from "../../components/ui/Kicker";
import { useI18n } from "../../i18n/i18n";
import { diseasePath } from "../../lib/diseasePath";
import { buildMapLinks } from "../../lib/geo";
import type { DiseaseGuideResponse, GuideExpert } from "../../types";
import styles from "./guide.module.css";

export function ExpertDetailPage() {
  const { diseaseId: rawDisease, expertId: rawExpert } = useParams<{
    diseaseId: string;
    expertId: string;
  }>();
  const diseaseId = decodeURIComponent(rawDisease || "").trim();
  const expertId = decodeURIComponent(rawExpert || "").trim();
  const { t, lang } = useI18n();

  const [guide, setGuide] = useState<DiseaseGuideResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!diseaseId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchGuide(diseaseId, lang)
      .then((data) => {
        if (!cancelled) setGuide(data);
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message || t("guide_err"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [diseaseId, lang, t]);

  if (loading) {
    return <div className={styles.loading}>{t("guide_loading")}</div>;
  }
  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.error}>{error}</div>
        <Link className={styles.backLink} to={diseasePath(diseaseId)}>
          ← {t("expert_back")}
        </Link>
      </div>
    );
  }

  const expert: GuideExpert | undefined = guide?.experts.find((e) => e.id === expertId);
  if (!guide || !expert) {
    return (
      <div className={styles.page}>
        <div className={styles.error}>{t("expert_not_found")}</div>
        <Link className={styles.backLink} to={diseasePath(diseaseId)}>
          ← {t("expert_back")}
        </Link>
      </div>
    );
  }

  const typeLabel = (type: string) => {
    if (type === "team") return t("guide_type_team");
    if (type === "department") return t("guide_type_dept");
    return t("guide_type_doctor");
  };

  const mapName = expert.hospital.map_query || expert.hospital.name;
  const links = buildMapLinks(mapName, expert.hospital.city);

  return (
    <div className={styles.page}>
      <div className={styles.backRow}>
        <Link className={styles.backLink} to={diseasePath(diseaseId)}>
          ← {t("expert_back")}
        </Link>
      </div>

      <section className={styles.hero}>
        <Kicker>{t("expert_kicker")}</Kicker>
        <div className={styles.detailHeroRow}>
          {expert.hospital.logo_url ? (
            <img className={styles.detailLogo} src={expert.hospital.logo_url} alt="" />
          ) : (
            <div className={styles.detailLogoFallback} aria-hidden>
              {t("guide_logo_soon")}
            </div>
          )}
          <div className={styles.detailHeroText}>
            <div className={styles.cardTop}>
              <h1 className={styles.namePrimary}>{expert.name}</h1>
              <span className={styles.typeBadge}>{typeLabel(expert.type)}</span>
            </div>
            <div className={styles.hospitalRow}>
              {expert.hospital.name}
              <span style={{ color: "var(--ink-faint)" }}> · {expert.hospital.city}</span>
            </div>
            {guide.name ? (
              <div className={styles.nameSecondary}>
                {guide.name}
                {guide.disease_id ? ` · ${guide.disease_id}` : ""}
              </div>
            ) : null}
          </div>
        </div>
      </section>

      {expert.hospital.advantage ? (
        <section className={styles.panel}>
          <h2 className={styles.sectionTitle}>{t("expert_advantage")}</h2>
          <p className={styles.advantage}>{expert.hospital.advantage}</p>
        </section>
      ) : null}

      {expert.bio ? (
        <section className={styles.panel}>
          <h2 className={styles.sectionTitle}>{t("expert_bio")}</h2>
          <p className={styles.bio}>{expert.bio}</p>
        </section>
      ) : null}

      <section className={styles.panel}>
        <h2 className={styles.sectionTitle}>{t("expert_maps")}</h2>
        <div className={styles.mapLinks}>
          <a className={styles.mapLink} href={links.amap} target="_blank" rel="noreferrer">
            {t("map_amap")}
          </a>
          <a className={styles.mapLink} href={links.baidu} target="_blank" rel="noreferrer">
            {t("map_baidu")}
          </a>
          <a className={styles.mapLink} href={links.google} target="_blank" rel="noreferrer">
            {t("map_google")}
          </a>
          <a className={styles.mapLink} href={links.apple} target="_blank" rel="noreferrer">
            {t("map_apple")}
          </a>
        </div>
      </section>
    </div>
  );
}
