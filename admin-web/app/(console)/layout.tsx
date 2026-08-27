"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

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

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">Origami Server</div>
        <nav className="sidebar-nav">
          <Link href="/dashboard" className={pathname === "/dashboard" ? "active" : ""}>
            Platform Dashboard
          </Link>
          <Link href="/tenants" className={pathname.startsWith("/tenants") ? "active" : ""}>
            Tenants
          </Link>
        </nav>
        <div style={{ marginTop: "auto", fontSize: "0.8rem", color: "var(--farmos-wheat)" }}>
          <div style={{ marginBottom: 8 }}>{me.display_name || me.email}</div>
          <button
            className="btn btn-secondary"
            style={{ width: "100%" }}
            onClick={logout}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
