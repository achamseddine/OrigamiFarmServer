/** A lifecycle status, as a chip.
 *
 * Dot plus word, never a dot alone: the accessibility line in the component
 * spec — "status is never conveyed by colour alone" — is the whole reason
 * the label is printed beside the tint rather than replaced by it. The word
 * is also title-cased here, because a table of SHOUTING enum values reads
 * as a database dump rather than a state.
 */

const CLASS_BY_STATUS: Record<string, string> = {
  ACTIVE: "chip-active",
  TRIAL: "chip-trial",
  ONBOARDING: "chip-trial",
  GRACE: "chip-grace",
  SUSPENDED: "chip-suspended",
  REVOKED: "chip-revoked",
  LOST: "chip-revoked",
  TERMINATED: "chip-terminated",
  RETIRED: "chip-terminated",
  INACTIVE: "chip-terminated",
};

function readable(status: string): string {
  return status.charAt(0) + status.slice(1).toLowerCase().replace(/_/g, " ");
}

export function StatusChip({ status }: { status: string }) {
  const cls = CLASS_BY_STATUS[status] || "chip-trial";
  return (
    <span className={`chip ${cls}`}>
      <span className="dot" aria-hidden="true" />
      {readable(status)}
    </span>
  );
}
