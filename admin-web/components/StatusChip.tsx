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

export function StatusChip({ status }: { status: string }) {
  const cls = CLASS_BY_STATUS[status] || "chip-trial";
  return <span className={`chip ${cls}`}>{status}</span>;
}
