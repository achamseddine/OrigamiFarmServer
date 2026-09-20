"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { Icon } from "./Icon";
import { VersionStamp } from "@/lib/version";

/** The profile side sheet, anchored from the avatar.
 *
 * Ordered the way 07_components/component_spec.md asks for it: who you are
 * and what you are allowed to do first, then the destinations, then Logout
 * on its own at the bottom in the danger colour — far enough from the rest
 * that nobody signs out while reaching for Settings.
 */

export interface ProfileSheetProps {
  displayName: string;
  email: string;
  roles: string[];
  /** Shown as a warning when the password in force was chosen by someone else. */
  passwordSetBySomeoneElse?: boolean;
  onClose: () => void;
  onLogout: () => void;
}

function initial(name: string, email: string): string {
  const source = name.trim() || email.trim();
  return (source[0] ?? "?").toUpperCase();
}

function readableRole(role: string): string {
  return role.replace("PLATFORM_", "").replace(/_/g, " ").toLowerCase();
}

export function ProfileSheet({
  displayName,
  email,
  roles,
  passwordSetBySomeoneElse,
  onClose,
  onLogout,
}: ProfileSheetProps) {
  const sheet = useRef<HTMLDivElement>(null);

  // Escape closes it, and focus moves into the sheet so a keyboard user is
  // not left tabbing through the page behind the scrim.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    sheet.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <button className="scrim" aria-label="Close the profile menu" onClick={onClose} />
      <div
        className="sheet"
        ref={sheet}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Your account"
      >
        <div className="sheet-id">
          <span className="avatar" aria-hidden="true">
            {initial(displayName, email)}
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="n">{displayName || email}</div>
            <div className="e">{email}</div>
          </div>
        </div>

        <div className="sheet-scope">
          {roles.length === 0 ? (
            <span className="chip chip-terminated">
              <span className="dot" />
              No platform role
            </span>
          ) : (
            roles.map((role) => (
              <span key={role} className="chip chip-active">
                <span className="dot" />
                {readableRole(role)}
              </span>
            ))
          )}
        </div>

        {passwordSetBySomeoneElse && (
          <div className="notice-banner" style={{ marginTop: 14, marginBottom: 0 }}>
            <Icon name="key" size={18} />
            <span>
              Your password was chosen by whoever created this account. Change it from My
              account.
            </span>
          </div>
        )}

        <div className="sheet-links">
          <Link href="/account" onClick={onClose}>
            <Icon name="user" />
            My account
            <span className="tail">
              <Icon name="chevron-right" size={18} />
            </span>
          </Link>
          <Link href="/staff" onClick={onClose}>
            <Icon name="people" />
            Staff &amp; access
            <span className="tail">
              <Icon name="chevron-right" size={18} />
            </span>
          </Link>
          <Link href="/catalog" onClick={onClose}>
            <Icon name="cart" />
            Subscription
            <span className="tail">
              <Icon name="chevron-right" size={18} />
            </span>
          </Link>
          <Link href="/guide" onClick={onClose}>
            <Icon name="sun" />
            Getting started
            <span className="tail">
              <Icon name="chevron-right" size={18} />
            </span>
          </Link>
        </div>

        <div className="sheet-links sheet-out">
          <button onClick={onLogout}>
            <Icon name="logout" />
            Sign out
          </button>
        </div>

        <div className="sheet-foot">
          <VersionStamp align="center" />
        </div>
      </div>
    </>
  );
}
