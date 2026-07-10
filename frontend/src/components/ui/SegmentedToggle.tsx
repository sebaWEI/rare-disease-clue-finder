import type { ReactNode } from "react";
import styles from "./ui.module.css";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
}

interface SegmentedToggleProps<T extends string> {
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
}

export function SegmentedToggle<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: SegmentedToggleProps<T>) {
  return (
    <div className={styles.segmented} role="tablist">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={disabled}
            className={`${styles.segment} ${active ? styles.segmentActive : ""}`}
            onClick={() => onChange(opt.value)}
          >
            {opt.icon}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
