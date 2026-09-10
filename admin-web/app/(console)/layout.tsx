"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

const NAV: { section: string; items: { href: string; label: string }[] }[] = [
  {
    section: "Monitor",
    items: [
      { href: "/dashboard", label: "Overview" },
      { href: "/usage", label: "Usage" },
      { href: "/licensing", label: "Licensing" },
    ],
  },
  {
    section: "Manage",
    items: [
      { href: "/tenants", label: "Tenants" },
      { href: "/catalog", label: "Plans & modules" },
      { href: "/staff", label: "Staff & access" },
    ],
  },
  {
    section: "Review",
    items: [
      { href: "/audit", label: "Audit log" },
      { href: "/account", label: "My account" },
    ],
  },
];

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const { me, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !me) {
      router.replace("/login");
    }
  }, [loading, me, router]);

  if (loading || !me) {
    return <div style={{ padding: 40 }}>Loading…</div>;
  }

  // A valid sign-in with no staff role would otherwise render a console
  // where every panel fails with PLATFORM_ROLE_REQUIRED. Say so once instead.
  if (me.platform_roles.length === 0) {
    return (
      <div style={{ padding: 40, maxWidth: 560 }}>
        <h1 className="page-title">No platform access</h1>
        <p className="page-subtitle">
          You are signed in as <strong>{me.email}</strong>, but this account holds no
          Origami platform role, so none of the console is available to it.
        </p>
        <p className="page-subtitle">
          A platform super admin can grant one from Staff &amp; access.
        </p>
        <button className="btn btn-secondary" onClick={logout}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">Origami Server</div>
        <nav className="sidebar-nav">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="sidebar-section">{group.section}</div>
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={
                    pathname === item.href || pathname.startsWith(`${item.href}/`) ? "active" : ""
                  }
                >
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div style={{ marginTop: "auto", fontSize: "0.8rem", color: "var(--farmos-wheat)" }}>
          <div style={{ marginBottom: 2 }}>{me.display_name || me.email}</div>
          <div style={{ marginBottom: 8, opacity: 0.7, fontSize: "0.7rem" }}>
            {me.platform_roles.map((role) => role.replace("PLATFORM_", "").replace(/_/g, " ").toLowerCase()).join(", ")}
          </div>
          <button className="btn btn-secondary" style={{ width: "100%" }} onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
