"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { TenantListResponse } from "@/lib/types";
import { StatusChip } from "@/components/StatusChip";

const PAGE_SIZE = 20;

export default function TenantsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const q = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const offset = Number(searchParams.get("offset") || 0);

  const [data, setData] = useState<TenantListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState(q);

  function updateParams(next: Record<string, string>) {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(next).forEach(([key, value]) => {
      if (value) params.set(key, value);
      else params.delete(key);
    });
    router.push(`/tenants?${params.toString()}`);
  }

  useEffect(() => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (status) params.set("status", status);
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(offset));

    apiFetch<TenantListResponse>(`/platform/v1/tenants?${params.toString()}`)
      .then(setData)
      .catch((err) => setError(err.message));
  }, [q, status, offset]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">Tenants</h1>
          <p className="page-subtitle">Every customer company, searchable and filterable.</p>
        </div>
        <Link href="/tenants/new" className="btn btn-primary">
          + Create Tenant
        </Link>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="toolbar">
        <input
          placeholder="Search company name or code…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") updateParams({ q: searchInput, offset: "0" });
          }}
          style={{ width: 280 }}
        />
        <button className="btn btn-secondary" onClick={() => updateParams({ q: searchInput, offset: "0" })}>
          Search
        </button>
        <select value={status} onChange={(e) => updateParams({ status: e.target.value, offset: "0" })}>
          <option value="">All statuses</option>
          <option value="ONBOARDING">Onboarding</option>
          <option value="TRIAL">Trial</option>
          <option value="ACTIVE">Active</option>
          <option value="GRACE">Grace</option>
          <option value="SUSPENDED">Suspended</option>
          <option value="TERMINATED">Terminated</option>
        </select>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Company ID</th>
              <th>Name</th>
              <th>Status</th>
              <th>Country</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((tenant) => (
              <tr
                key={tenant.id}
                className="clickable"
                onClick={() => router.push(`/tenants/${tenant.id}`)}
              >
                <td>{tenant.company_code}</td>
                <td>{tenant.display_name}</td>
                <td>
                  <StatusChip status={tenant.status} />
                </td>
                <td>{tenant.country}</td>
                <td>{new Date(tenant.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={5} style={{ textAlign: "center", color: "var(--farmos-muted)", padding: 24 }}>
                  No tenants match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > PAGE_SIZE && (
        <div className="toolbar">
          <button
            className="btn btn-secondary"
            disabled={offset === 0}
            onClick={() => updateParams({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}
          >
            Previous
          </button>
          <span style={{ color: "var(--farmos-muted)", fontSize: "0.85rem" }}>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <button
            className="btn btn-secondary"
            disabled={offset + PAGE_SIZE >= data.total}
            onClick={() => updateParams({ offset: String(offset + PAGE_SIZE) })}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
