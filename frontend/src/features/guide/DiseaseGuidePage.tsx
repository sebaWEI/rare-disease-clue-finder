import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchGuide } from "../../api/client";
import { Kicker } from "../../components/ui/Kicker";
import { useI18n } from "../../i18n/i18n";
import type { DiseaseGuideResponse } from "../../types";
import { ExpertMap } from "./ExpertMap";
import styles from "./guide.module.css";

type GeoMode = "locating" | "located" | "city" | "denied" | "unavailable";

export function DiseaseGuidePage() {
  const { diseaseId: rawId } = useParams<{ diseaseId: string }>();
  const diseaseId = decodeURIComponent(rawId || "").trim();
  const { t, lang } = useI18n();

  const [guide, setGuide] = useState<DiseaseGuideResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [geoMode, setGeoMode] = useState<GeoMode>("locating");
  const [userPos, setUserPos] = useState<{ lat: number; lng: number } | null>(null);

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

  const requestGeo = useCallback(() => {
    if (!navigator.geolocation) {
      setGeoMode("unavailable");
      return;
    }
    setGeoMode("locating");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setUserPos({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        setGeoMode("located");
      },
      () => {
        setGeoMode("denied");
        setUserPos(null);
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  }, []);

  useEffect(() => {
    requestGeo();
  }, [requestGeo]);

  const primaryName = guide?.name || diseaseId;
  const secondaryName =
    guide?.name_alt && guide.name_alt !== primaryName ? guide.name_alt : "";

  if (loading) {
    return <div className={styles.loading}>{t("guide_loading")}</div>;
  }
  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.error}>{error}</div>
        <Link className={styles.backLink} to="/">
          ← {t("guide_back")}
        </Link>
      </div>
    );
  }
  if (!guide) return null;

  return (
    <div className={styles.page}>
      <div className={styles.backRow}>
        <Link className={styles.backLink} to="/">
          ← {t("guide_back")}
        </Link>
      </div>

      <section className={styles.hero}>
        <Kicker>{t("guide_kicker")}</Kicker>
        <div className={styles.diseaseId}>{guide.disease_id}</div>
        <h1 className={styles.namePrimary}>{primaryName}</h1>
        {secondaryName ? <div className={styles.nameSecondary}>{secondaryName}</div> : null}
        {guide.summary ? <p className={styles.summary}>{guide.summary}</p> : null}
        {!guide.available && guide.message ? (
          <p className={styles.summary} style={{ marginTop: 12 }}>
            {guide.message}
          </p>
        ) : null}
      </section>

      {guide.available && (
        <section className={styles.panel}>
          <h2 className={styles.sectionTitle}>{t("guide_care_title")}</h2>
          <p className={styles.sectionDesc}>{t("guide_care_desc")}</p>
          <ul className={styles.tips}>
            {(guide.care_tips || []).map((tip, i) => (
              <li key={i} className={styles.tip}>
                <span className={styles.tipMark}>{i + 1}</span>
                <span>{tip}</span>
              </li>
            ))}
          </ul>
          {(guide.specialty_keywords || []).length > 0 && (
            <div className={styles.keywords}>
              {guide.specialty_keywords.map((k) => (
                <span key={k} className={styles.keyword}>
                  {k}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      <section className={styles.panel}>
        <h2 className={styles.sectionTitle}>{t("guide_experts_title")}</h2>
        <p className={styles.sectionDesc}>{t("guide_experts_desc")}</p>

        {!guide.available ? (
          <div className={styles.placeholder}>
            {guide.message || t("guide_placeholder")}{" "}
            <a
              className={styles.orphanLink}
              href={`https://www.orpha.net/en/disease/detail/${diseaseId.replace(/^ORPHA:/i, "")}`}
              target="_blank"
              rel="noreferrer"
            >
              Orphanet ↗
            </a>
          </div>
        ) : (
          <ExpertMap
            diseaseId={diseaseId}
            experts={guide.experts}
            userPos={userPos}
            geoMode={geoMode}
            onRequestGeo={requestGeo}
          />
        )}
      </section>
    </div>
  );
}
