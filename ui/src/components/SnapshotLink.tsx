import type { EvidenceRecord } from "../api";
import { STATE_LABEL } from "../format";

/** One click opens the snapshot (R13.2). Opens in a new tab: the server sandboxes it (CSP). */
export function SnapshotLink({ ev, label = "Open snapshot" }: { ev: EvidenceRecord; label?: string }) {
  if (!ev.snapshot.available || !ev.snapshot.href) {
    return (
      <span className="muted" title="The raw bytes are gone; hash, URL and fetch time are kept">
        {STATE_LABEL[ev.snapshot.state] ?? ev.snapshot.state}
      </span>
    );
  }
  return (
    <a href={ev.snapshot.href} target="_blank" rel="noopener noreferrer" className="snapshot-link">
      {label}
    </a>
  );
}
