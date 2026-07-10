import { useI18n } from "../../i18n/i18n";
import { Kicker } from "../../components/ui/Kicker";
import { Button } from "../../components/ui/Button";
import { SegmentedToggle } from "../../components/ui/SegmentedToggle";
import type { SegmentOption } from "../../components/ui/SegmentedToggle";
import type { HpoItem } from "../../hooks/useHpoSelection";
import type { PredictResponse, SmartSearchResponse, SuggestedHpo } from "../../types";
import { SmartMatchGroups } from "./SmartMatchGroups";
import { SelectedChips } from "./SelectedChips";
import { Suggestions } from "./Suggestions";
import { AutoResult } from "./AutoResult";
import styles from "./intake.module.css";

export type IntakeMode = "auto" | "manual";

interface IntakePanelProps {
  mode: IntakeMode;
  onModeChange: (mode: IntakeMode) => void;
  text: string;
  onTextChange: (text: string) => void;
  matching: boolean;
  running: boolean;
  onPrimaryAction: () => void;
  // manual
  smartData: SmartSearchResponse | null;
  selectedItems: HpoItem[];
  isSelected: (id: string) => boolean;
  onToggle: (id: string, name: string) => void;
  onRemove: (id: string) => void;
  suggestions: SuggestedHpo[];
  onRun: () => void;
  // auto
  autoData: PredictResponse | null;
  onViewReport: () => void;
}

function SparkIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M8 1.5 9.4 6l4.5 1.4-4.5 1.4L8 14.5 6.6 8.8 2 7.4 6.6 6 8 1.5Z" fill="currentColor" />
    </svg>
  );
}
function HandIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M5 8V3.5a1 1 0 1 1 2 0V7m0-.5V2.5a1 1 0 1 1 2 0V7m0-.5V3.5a1 1 0 1 1 2 0V8m0-1a1 1 0 1 1 2 0v3.5A3.5 3.5 0 0 1 9.5 15h-1A4.5 4.5 0 0 1 4 10.5V9"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function IntakePanel(props: IntakePanelProps) {
  const { t } = useI18n();
  const {
    mode,
    onModeChange,
    text,
    onTextChange,
    matching,
    running,
    onPrimaryAction,
    smartData,
    selectedItems,
    isSelected,
    onToggle,
    onRemove,
    suggestions,
    onRun,
    autoData,
    onViewReport,
  } = props;

  const options: SegmentOption<IntakeMode>[] = [
    { value: "auto", label: t("mode_auto"), icon: <SparkIcon /> },
    { value: "manual", label: t("mode_manual"), icon: <HandIcon /> },
  ];

  const busy = matching || running;

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <Kicker>{t("step_kicker")}</Kicker>
        <h2 className={styles.title}>{t("intake_title")}</h2>
        <p className={styles.desc}>{t("intake_desc")}</p>
      </div>

      <div className={styles.modeRow}>
        <SegmentedToggle
          value={mode}
          options={options}
          onChange={onModeChange}
          disabled={busy}
        />
        <span className={styles.modeHint}>
          {mode === "auto" ? t("mode_auto_hint") : t("mode_manual_hint")}
        </span>
      </div>

      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="narrative">
          {t("narrative_label")}
        </label>
        <textarea
          id="narrative"
          className={styles.textarea}
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder={t("smart_placeholder")}
        />
      </div>

      <div className={styles.actions}>
        <Button variant="primary" onClick={onPrimaryAction} loading={matching}>
          {matching
            ? t("btn_analysing")
            : mode === "auto"
              ? t("btn_auto")
              : t("btn_match")}
        </Button>
      </div>

      {mode === "auto" && autoData && (
        <AutoResult data={autoData} onViewReport={onViewReport} />
      )}

      {mode === "manual" && (
        <>
          {smartData && (
            <SmartMatchGroups data={smartData} isSelected={isSelected} onToggle={onToggle} />
          )}
          <SelectedChips items={selectedItems} onRemove={onRemove} />
          <Suggestions suggestions={suggestions} isSelected={isSelected} onToggle={onToggle} />
          <div className={styles.actions}>
            <Button
              variant="brand"
              block
              onClick={onRun}
              loading={running}
              disabled={selectedItems.length === 0}
            >
              {t("btn_run")} →
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
