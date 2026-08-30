/**
 * The collapsible row the whole workspace is built from: chevron, icon, label, detail.
 * Shared so the live-feed panels wear the same chrome as the pipeline stages.
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export type ActivityProps = {
  icon: React.ReactNode;
  title: string;
  detail: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  /** Re-assert `defaultOpen` when this changes, so a panel can open itself when the
   *  thing it shows starts happening without pinning the user's choice afterwards. */
  openKey?: string;
};

export default function Activity({
  icon,
  title,
  detail,
  children,
  defaultOpen = false,
  openKey,
}: ActivityProps) {
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (openKey !== undefined) setOpen(defaultOpen);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openKey]);

  return (
    <section className="chat-activity">
      <button
        className="activity-toggle"
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="activity-chevron" aria-hidden="true">
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
        <span className="activity-icon">{icon}</span>
        <span className="activity-label">
          <strong>{title}</strong>
          <small>{detail}</small>
        </span>
      </button>
      {open && <div className="activity-content">{children}</div>}
    </section>
  );
}
