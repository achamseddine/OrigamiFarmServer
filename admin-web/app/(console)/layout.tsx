"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ServerPill, VersionStamp } from "@/lib/version";
import { BrandMark } from "@/components/Brand";
import { Icon, IconName } from "@/components/Icon";
import { ProfileSheet } from "@/components/ProfileSheet";

/** The console shell.
 *
 * Two pieces of chrome, as the redesign has them: destinations down the
 * side, and a top utility cluster carrying platform state and identity.
 * The tablet app puts its destinations in a bottom bar because it is held
 * in two hands; this is a desk tool with nine of them, so they run down a
 * rail instead — the hierarchy the spec asks for, in the form the device
 * can carry.
 */

const NAV: { section: string; items: { href: string; label: string; icon: IconName }[] }[] = [
  // First, and on its own: someone opening this console for the first time
  // has no way to know which of the screens below to touch first.
  {
    section: "Start here",
    items: [{ href: "/guide", label: "Getting started", icon: "sun" }],
  },
  {
    section: "Monitor",
    items: [
      { href: "/dashboard", label: "Overview", icon: "chart-line" },
      { href: "/business", label: "Business", icon: "coins" },
      { href: "/usage", label: "Usage", icon: "package" },
    ],
  },
  {
    section: "Manage",
    items: [
      { href: "/tenants", label: "Tenants", icon: "barn" },
      { href: "/catalog", label: "Subscription", icon: "cart" },
      { href: "/staff", label: "Staff & access", icon: "people" },
    ],
  },
  {
    section: "Review",
    items: [
      { href: "/audit", label: "Audit log", icon: "report" },
      { href: "/account", label: "My account", icon: "user" },
    ],
  },
];

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const { me, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [profileOpen, setProfileOpen] = useState(false);

  useEffect(() => {
    if (!loading && !me) {
      router.replace("/login");
    }
  }, [loading, me, router]);

  // A route change with the sheet still open would leave it floating over
  // a screen it no longer belongs to.
  useEffect(() => {
    setProfileOpen(false);
  }, [pathname]);

  if (loading || !me) {
    return (
      <div className="entry-solo">
        <div className="panel" style={{ textAlign: "center" }}>
          <BrandMark size={44} />
          <p className="page-subtitle" style={{ marginTop: 12 }}>
            Loading the console…
          </p>
        </div>
      </div>
    );
  }

  // A valid sign-in with no staff role would otherwise render a console
  // where every panel fails with PLATFORM_ROLE_REQUIRED. Say so once instead.
  if (me.platform_roles.length === 0) {
    return (
      <div className="entry-solo">
        <div className="panel">
          <BrandMark size={44} />
          <h1 className="page-title" style={{ marginTop: 12 }}>
            No platform access
          </h1>
          <p className="page-subtitle" style={{ marginBottom: 14 }}>
            You are signed in as <strong>{me.email}</strong>, but this account holds no Origami
            platform role, so none of the console is available to it.
          </p>
          <p className="page-subtitle" style={{ marginBottom: 14 }}>
            A platform super admin can grant one from Staff &amp; access.
          </p>
          {me.password_set_by_someone_else && (
            <p className="page-subtitle" style={{ marginBottom: 14 }}>
              Whoever created this account also chose its password, so change it once you have
              access.
            </p>
          )}
          <button className="btn btn-secondary" onClick={logout}>
            <Icon name="logout" />
            Sign out
          </button>
        </div>
      </div>
    );
  }

  const initial = (me.display_name || me.email || "?").trim()[0]?.toUpperCase() ?? "?";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/dashboard" className="sidebar-brand">
          <BrandMark size={34} className="mark" />
          <span className="words">
            <span className="n">Origami</span>
            <span className="s">Server console</span>
          </span>
        </Link>

        <nav className="sidebar-nav" aria-label="Console sections">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="sidebar-section">{group.section}</div>
              {group.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={active ? "active" : ""}
                    aria-current={active ? "page" : undefined}
                  >
                    <Icon name={item.icon} />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <VersionStamp />
        </div>
      </aside>

      <div style={{ minWidth: 0 }}>
        <header className="appbar">
          <div className="appbar-cluster">
            <ServerPill />
          </div>
          <div className="appbar-cluster">
            <Link href="/audit" className="icon-btn" aria-label="Audit log">
              <Icon name="bell" size={22} />
            </Link>
            <button
              className="avatar-btn"
              onClick={() => setProfileOpen((open) => !open)}
              aria-haspopup="dialog"
              aria-expanded={profileOpen}
            >
              <span className="avatar" aria-hidden="true">
                {initial}
              </span>
              <span style={{ fontSize: "0.86rem", fontWeight: 600 }}>
                {me.display_name || me.email}
              </span>
              <Icon name="chevron-down" size={18} />
            </button>
          </div>
        </header>

        {profileOpen && (
          <ProfileSheet
            displayName={me.display_name}
            email={me.email}
            roles={me.platform_roles}
            passwordSetBySomeoneElse={me.password_set_by_someone_else}
            onClose={() => setProfileOpen(false)}
            onLogout={logout}
          />
        )}

        <main className="main-content">{children}</main>
      </div>
    </div>
  );
}
