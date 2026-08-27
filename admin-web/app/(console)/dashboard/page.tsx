"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { DashboardSummary } from "@/lib/types";

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<DashboardSummary>("/platform/v1/dashboard/summary")
      .then(setSummary)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div>
      <h1 className="page-title">Platform Dashboard</h1>
      <p className="page-subtitle">Live counts from the control plane — every card navigates to the underlying data.</p>

      {error && <div className="error-banner">{error}</div>}

      {summary && (
        <div className="card-grid">
          <Link href="/tenants?status=ACTIVE" className="stat-card">
            <div className="value">{summary.active_tenants}</div>
            <div className="label">Active tenants</div>
          </Link>
          <Link href="/tenants?status=TRIAL" className="stat-card">
            <div className="value">{summary.trial_tenants}</div>
            <div className="label">Trial / onboarding tenants</div>
          </Link>
          <Link href="/tenants?status=SUSPENDED" className="stat-card">
            <div className="value">{summary.suspended_tenants}</div>
            <div className="label">Suspended tenants</div>
          </Link>
          <Link href="/tenants" className="stat-card">
            <div className="value">{summary.active_devices}</div>
            <div className="label">Active devices</div>
          </Link>
          <Link href="/tenants" className="stat-card">
            <div className="value">{summary.renewals_due_30d}</div>
            <div className="label">Renewals due (30d)</div>
          </Link>
          <Link href="/tenants" className="stat-card">
            <div className="value">{summary.backup_failures}</div>
            <div className="label">Backup failures</div>
          </Link>
        </div>
      )}
    </div>
  );
}
