import { useI18n } from "../../i18n/i18n";
import ui from "../ui/ui.module.css";

export function LangToggle() {
  const { lang, setLang } = useI18n();
  return (
    <div className={ui.lang} role="group" aria-label="Language">
      <button
        type="button"
        className={`${ui.langBtn} ${lang === "en" ? ui.langActive : ""}`}
        aria-pressed={lang === "en"}
        onClick={() => setLang("en")}
      >
        EN
      </button>
      <button
        type="button"
        className={`${ui.langBtn} ${lang === "zh" ? ui.langActive : ""}`}
        aria-pressed={lang === "zh"}
        onClick={() => setLang("zh")}
      >
        中文
      </button>
    </div>
  );
}
