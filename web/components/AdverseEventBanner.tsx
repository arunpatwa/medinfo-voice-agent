"use client";

import type { AdverseEventPayload } from "@/lib/types";

interface Props {
  events: AdverseEventPayload[];
  onDismiss: (index: number) => void;
}

const SEVERITY_LABEL: Record<AdverseEventPayload["severity"], string> = {
  serious: "Serious",
  non_serious: "Non-serious",
  unknown: "Severity unknown",
};

/**
 * Surfaces adverse events the agent flagged.
 *
 * Deliberately loud and not auto-dismissing: in a real deployment this is the
 * trigger for a pharmacovigilance case, which carries a 24-hour reporting
 * obligation. It should be impossible to miss.
 */
export function AdverseEventBanner({ events, onDismiss }: Props) {
  if (events.length === 0) return null;

  return (
    <div className="ae" role="alert">
      {events.map((e, i) => (
        <div key={`${e.term}-${i}`} className={`ae__item ae__item--${e.severity}`}>
          <div className="ae__body">
            <p className="ae__title">
              Adverse event logged — {e.term}
              <span className="ae__sev">{SEVERITY_LABEL[e.severity]}</span>
            </p>
            <p className="ae__verbatim">&ldquo;{e.verbatim}&rdquo;</p>
            <p className="ae__note">
              Recorded to the audit trail for safety reporting. Demo only — no
              case is actually filed.
            </p>
          </div>
          <button
            className="ae__dismiss"
            onClick={() => onDismiss(i)}
            aria-label="Dismiss adverse event notice"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
