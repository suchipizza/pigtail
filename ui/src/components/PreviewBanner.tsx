/** D1 preview acceptance: every page is labelled "uncoded preview". */
export function PreviewBanner() {
  return (
    <div className="preview-banner" role="note" aria-label="Uncoded preview">
      <strong>Uncoded preview</strong> — events are raw captures, not coded. Burst/launch labels,
      triggers and patterns arrive with a brief's deep forensics (M15).
    </div>
  );
}
