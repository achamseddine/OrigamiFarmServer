"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { VersionStamp } from "@/lib/version";
import { BrandLogo } from "@/components/Brand";
import { Icon } from "@/components/Icon";

/** The sign-in screen, built to the redesign's split: the Bekaa valley on
 *  one side and the credential card on the other.
 *
 *  The scenery is the package's reusable vector background, not the
 *  flattened mockup — 04_backgrounds/README.md is explicit that the render
 *  must not be shipped as a UI background.
 */

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
    } catch (err) {
      // 401 is the expected "wrong credentials" case and the API returns
      // the same message whether the address exists or not; anything else
      // is a real fault worth showing verbatim.
      if (err instanceof ApiError && err.status === 401) {
        setError("Incorrect email or password.");
      } else if (err instanceof ApiError) {
        setError(`${err.code}: ${err.message}`);
      } else {
        setError("Could not reach the server. Check your connection and try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="entry">
      <section className="entry-scene">
        <h2>Smarter farming for a better future</h2>
        <div className="rule" />
        <p className="lede">
          The control plane behind every Origami farm — tenants, subscriptions and the people
          who run them.
        </p>
        <div className="entry-points">
          <div>
            <Icon name="leaf" size={18} />
            More productive farms
          </div>
          <div>
            <Icon name="people" size={18} />
            Stronger communities
          </div>
          <div>
            <Icon name="location" size={18} />
            Bekaa Valley, Lebanon
          </div>
        </div>
      </section>

      <section className="entry-panel">
        <form onSubmit={handleSubmit} className="entry-card">
          <BrandLogo size={46} className="brand" />

          <h1>
            <Icon name="sun" size={26} />
            Start your day
          </h1>
          <p className="lede">Platform admin console</p>

          {error && (
            <div className="error-banner" role="alert">
              <Icon name="warning" size={18} />
              <span>{error}</span>
            </div>
          )}

          <div className="field-row">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="you@example.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field-row">
            <label htmlFor="password">Password</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                required
                style={{ flex: 1 }}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {/* Typing a long generated password blind, on a screen in
                  daylight, is how people end up locked out. */}
              <button
                type="button"
                className="icon-btn"
                onClick={() => setShowPassword((on) => !on)}
                aria-label={showPassword ? "Hide the password" : "Show the password"}
              >
                <Icon name="eye" size={20} />
              </button>
            </div>
          </div>

          <button type="submit" className="btn btn-primary btn-fold" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
            {!submitting && <Icon name="arrow-right" size={20} />}
          </button>

          <div className="entry-foot">
            Staff accounts are created with <code>scripts/create_platform_admin.py</code>, which
            is also how a lost password is reset.
            {/* Before anyone signs in, because "is the new build up?" is
                asked most often by someone who cannot get in yet. */}
            <div style={{ marginTop: 12 }}>
              <VersionStamp />
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}
