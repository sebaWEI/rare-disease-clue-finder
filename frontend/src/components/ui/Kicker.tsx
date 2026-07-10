import styles from "./ui.module.css";

export function Kicker({ children }: { children: string }) {
  return (
    <span className={styles.kicker}>
      <span className={styles.kickerDot} aria-hidden />
      {children}
    </span>
  );
}
