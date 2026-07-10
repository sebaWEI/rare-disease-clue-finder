import styles from "./layout.module.css";

export function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <span>HPO v2026-02-16 · ORDO v4.8 · Orphanet · CC-BY-4.0</span>
        <span>Rare Disease Pre-diagnosis Clue Finder · iGEM 2026 PekingHSC</span>
      </div>
    </footer>
  );
}
