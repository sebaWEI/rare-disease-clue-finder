import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Header } from "./components/layout/Header";
import { AssuranceBar } from "./components/layout/AssuranceBar";
import { Footer } from "./components/layout/Footer";
import { IntakePanel, type IntakeMode } from "./features/intake/IntakePanel";
import { ClueReport } from "./features/report/ClueReport";
import { useI18n } from "./i18n/i18n";
import { useHpoSelection } from "./hooks/useHpoSelection";
import { autoDiagnose, predict, smartSearch } from "./api/client";
import { autoSelectionItems, resolveReportSymptoms } from "./lib/symptoms";
import type { PredictResponse, SmartSearchResponse, SuggestedHpo } from "./types";
import layout from "./components/layout/layout.module.css";

const MIN_CHARS = 3;

export default function App() {
  const { t, lang } = useI18n();
  const selection = useHpoSelection();

  const [view, setView] = useState<"intake" | "report">("intake");
  const [mode, setMode] = useState<IntakeMode>("auto");
  const [text, setText] = useState("");

  const [autoData, setAutoData] = useState<PredictResponse | null>(null);
  const [reportData, setReportData] = useState<PredictResponse | null>(null);
  const [smartData, setSmartData] = useState<SmartSearchResponse | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestedHpo[]>([]);

  const [matching, setMatching] = useState(false);
  const [running, setRunning] = useState(false);

  const { items, has, toggle, remove, clear, setAll } = selection;

  // Results are language-specific — reset derived state when language changes.
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setAutoData(null);
    setReportData(null);
    setSmartData(null);
    setSuggestions([]);
    clear();
    setView("intake");
  }, [lang, clear]);

  // Background suggestions ("do you also have…") while picking symptoms.
  const suggestSeq = useRef(0);
  useEffect(() => {
    if (mode !== "manual" || view !== "intake" || items.length === 0) {
      setSuggestions([]);
      return;
    }
    const seq = ++suggestSeq.current;
    const ids = items.map((it) => it.id);
    const timer = window.setTimeout(() => {
      predict(ids, lang)
        .then((resp) => {
          if (seq === suggestSeq.current) setSuggestions(resp.suggested_hpos || []);
        })
        .catch(() => {});
    }, 400);
    return () => window.clearTimeout(timer);
  }, [items, mode, view, lang]);

  const handleModeChange = useCallback((next: IntakeMode) => {
    setMode(next);
    setAutoData(null);
    setSmartData(null);
    setSuggestions([]);
  }, []);

  const runAuto = useCallback(async () => {
    const value = text.trim();
    if (value.length < MIN_CHARS) {
      alert(t("err_min_chars"));
      return;
    }
    setMatching(true);
    try {
      const resp = await autoDiagnose(value, lang);
      setAutoData(resp);
    } catch (err) {
      alert(`${t("err_auto")}: ${(err as Error).message}`);
    } finally {
      setMatching(false);
    }
  }, [text, lang, t]);

  const runMatch = useCallback(async () => {
    const value = text.trim();
    if (value.length < MIN_CHARS) {
      alert(t("err_min_chars"));
      return;
    }
    setMatching(true);
    try {
      const resp = await smartSearch(value, lang);
      setSmartData(resp);
    } catch (err) {
      alert(`${t("err_match")}: ${(err as Error).message}`);
    } finally {
      setMatching(false);
    }
  }, [text, lang, t]);

  const runInference = useCallback(async () => {
    if (items.length === 0) return;
    setRunning(true);
    try {
      const resp = await predict(items.map((it) => it.id), lang);
      setReportData(resp);
      setSuggestions(resp.suggested_hpos || []);
      setView("report");
    } catch (err) {
      alert(`${(err as Error).message}`);
    } finally {
      setRunning(false);
    }
  }, [items, lang]);

  const viewAutoReport = useCallback(() => {
    if (!autoData) return;
    setReportData(autoData);
    setView("report");
  }, [autoData]);

  const backToEdit = useCallback(() => {
    if (mode === "auto" && autoData) {
      const derived = autoSelectionItems(autoData);
      if (derived.length > 0) setAll([...items, ...derived]);
    }
    setView("intake");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [mode, autoData, items, setAll]);

  const newSearch = useCallback(() => {
    setText("");
    clear();
    setAutoData(null);
    setReportData(null);
    setSmartData(null);
    setSuggestions([]);
    setView("intake");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [clear]);

  const reportSymptoms = useMemo(
    () => (reportData ? resolveReportSymptoms(reportData, items) : []),
    [reportData, items],
  );

  return (
    <>
      <Header />
      <AssuranceBar />
      <main className={layout.main}>
        {view === "intake" ? (
          <IntakePanel
            mode={mode}
            onModeChange={handleModeChange}
            text={text}
            onTextChange={setText}
            matching={matching}
            running={running}
            onPrimaryAction={mode === "auto" ? runAuto : runMatch}
            smartData={smartData}
            selectedItems={items}
            isSelected={has}
            onToggle={toggle}
            onRemove={remove}
            suggestions={suggestions}
            onRun={runInference}
            autoData={autoData}
            onViewReport={viewAutoReport}
          />
        ) : (
          reportData && (
            <ClueReport
              data={reportData}
              symptoms={reportSymptoms}
              originalText={text}
              onNew={newSearch}
              onEdit={backToEdit}
            />
          )
        )}
      </main>
      <Footer />
    </>
  );
}
