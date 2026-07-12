import { Link } from "react-router-dom";
import { useI18n } from "../../i18n/i18n";
import { Helix } from "../graphics/Helix";
import { Mark } from "../graphics/Mark";
import { LangToggle } from "./LangToggle";
import styles from "./layout.module.css";

export function Header() {
  const { t } = useI18n();
  return (
    <header className={styles.header}>
      <div className={styles.headerGrain} aria-hidden />
      <Helix className={styles.helix} />
      <div className={styles.headerInner}>
        <Link to="/" className={styles.brandRow} style={{ textDecoration: "none", color: "inherit" }}>
          <div className={styles.mark}>
            <Mark />
          </div>
          <div className={styles.brandText}>
            <h1 className={styles.title}>{t("brand_name")}</h1>
            <p className={styles.tagline}>{t("brand_tagline")}</p>
          </div>
        </Link>
        <div className={styles.headerRight}>
          <span className={styles.kickerBadge}>{t("brand_kicker")}</span>
          <LangToggle />
        </div>
      </div>
    </header>
  );
}
