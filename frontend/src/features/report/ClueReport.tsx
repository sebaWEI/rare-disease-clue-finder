import { useRef, useState } from "react";
import { useI18n } from "../../i18n/i18n";
import { Kicker } from "../../components/ui/Kicker";
import { Button } from "../../components/ui/Button";
import { formatTimestamp } from "../../lib/format";
import type { HpoItem } from "../../hooks/useHpoSelection";
import type { PredictResponse } from "../../types";
import { Disclaimer } from "./Disclaimer";
import { SymptomSummary } from "./SymptomSummary";
import { ClueCard } from "./ClueCard";
import { DescriptionCards } from "./DescriptionCards";
import styles from "./report.module.css";

interface ClueReportProps {
  data: PredictResponse;
  symptoms: HpoItem[];
  originalText: string;
  onNew: () => void;
  onEdit: () => void;
}

export function ClueReport({ data, symptoms, originalText, onNew, onEdit }: ClueReportProps) {
  const { t, lang } = useI18n();
  const contentRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState(false);
  const diseases = data.results || [];

  async function downloadPDF() {
    const el = contentRef.current;
    if (!el) return;
    setExporting(true);
    el.classList.add(styles.pdfExport);
    try {
      const html2pdf = (await import("html2pdf.js")).default;
      await html2pdf()
        .set({
          margin: [12, 10, 12, 10],
          filename: `RareDisease_Clue_Report_${new Date().toISOString().slice(0, 10)}.pdf`,
          image: { type: "jpeg", quality: 0.95 },
          html2canvas: { scale: 2, useCORS: true, logging: false, backgroundColor: "#ffffff" },
          jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
          pagebreak: { mode: ["avoid-all", "css", "legacy"] },
        } as Record<string, unknown>)
        .from(el)
        .save();
    } catch (err) {
      console.error("PDF export failed:", err);
      alert(t("err_pdf"));
    } finally {
      el.classList.remove(styles.pdfExport);
      setExporting(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.bar}>
        <div>
          <Kicker>{t("report_kicker")}</Kicker>
          <h2 className={styles.title}>{t("report_title")}</h2>
        </div>
        <div className={styles.barActions}>
          <Button variant="ghost" onClick={downloadPDF} loading={exporting}>
            ⤓ {t("btn_pdf")}
          </Button>
          <Button variant="ghost" onClick={onEdit}>
            ← {t("btn_edit")}
          </Button>
          <Button variant="ghost" onClick={onNew}>
            {t("btn_new")}
          </Button>
        </div>
      </div>

      <div ref={contentRef} className={styles.wrap}>
        <Disclaimer />

        <SymptomSummary symptoms={symptoms} />

        <div className={styles.panel}>
          <div className={styles.sectionTitle}>
            <span className={styles.sectionNum}>02</span>
            {t("section_clues")}
          </div>
          <div className={styles.sectionDesc}>{t("section_clues_desc")}</div>
          <div className={styles.clues}>
            {diseases.map((d, i) => (
              <ClueCard key={d.disease_id} disease={d} index={i} />
            ))}
          </div>
        </div>

        <DescriptionCards data={data} originalText={originalText} />

        <div className={styles.generated}>
          {t("report_generated")} {formatTimestamp(lang)} · iGEM 2026 PekingHSC
        </div>
      </div>
    </div>
  );
}
