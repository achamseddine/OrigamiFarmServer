"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

/** The old address of the welcome page, kept alive.
 *
 * This page moved to /welcome because the product already had "device
 * activation codes" for pairing a tablet, and having two different
 * credentials both called activation is a trap: an admin pasted a pairing
 * code into this URL and reasonably expected it to work.
 *
 * Renaming a URL that has been emailed to customers is not free, so this
 * forwards instead of disappearing. Any invitation sent before the rename
 * still lands somewhere that works.
 */
function Forward() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const token = params.get("token");
    router.replace(token ? `/welcome/?token=${encodeURIComponent(token)}` : "/welcome/");
  }, [params, router]);

  return null;
}

export default function ActivateRedirectPage() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "var(--farmos-stone)",
        color: "var(--farmos-muted)",
        fontSize: "0.9rem",
      }}
    >
      {/* useSearchParams needs a Suspense boundary or the static export
          fails to prerender this route. */}
      <Suspense fallback={<span>Loading…</span>}>
        <Forward />
      </Suspense>
      <span>Taking you to your account…</span>
    </div>
  );
}
