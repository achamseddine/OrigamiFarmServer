"use client";

import { useState } from "react";
import { ErrorBanner, Loading, PageHeader, formatDateTime, useResource } from "@/lib/ui";
import { AuditEventItem } from "@/lib/types";

const LIMITS = [50, 100, 200];

export default function AuditPage() {
  const [limit, setLimit] = useState(50);
  const { data, error, loading } = useResource<AuditEventItem[]>(
    `/platform/v1/audit-events?limit=${limit}`
  );

  return (
    <div>
      <PageHeader
        title="Audit log"
        subtitle="Every consequential action across the platform, newest first — appended, never edited."
      />
      <ErrorBanner message={error} />

      <div className="panel">
        <div className="toolbar">
          <label htmlFor="audit-limit" style={{ fontSize: "0.8rem", color: "var(--farmos-muted)" }}>
            Show
          </label>
          <select id="audit-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {LIMITS.map((option) => (
              <option key={option} value={option}>
                {option} most recent
              </option>
            ))}
          </select>
        </div>

        {loading && <Loading what="the audit log" />}
        {data &&
          (data.length === 0 ? (
            <div className="empty-note">Nothing recorded yet.</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Action</th>
                    <th>Entity</th>
                    <th>Actor</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((event) => (
                    <tr key={event.id}>
                      <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(event.created_at)}</td>
                      <td>
                        <code>{event.action}</code>
                      </td>
                      <td>
                        {event.entity_type}
                        {event.entity_id && (
                          <div style={{ color: "var(--farmos-muted)", fontSize: "0.72rem" }}>
                            {event.entity_id}
                          </div>
                        )}
                      </td>
                      <td style={{ fontSize: "0.78rem", color: "var(--farmos-muted)" }}>
                        {event.actor_type}
                      </td>
                      <td style={{ fontSize: "0.78rem" }}>{event.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>
    </div>
  );
}
