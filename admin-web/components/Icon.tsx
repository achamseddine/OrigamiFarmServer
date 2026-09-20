/** The Origami FarmOS v1 line-icon set, inlined.
 *
 * The design package ships these as individual SVG files, but the console
 * is a static export served from the API container, and thirty-odd extra
 * requests for 300-byte files — each one able to fail on its own and leave
 * a hole in the chrome — buys nothing. Inlining also means an icon takes
 * its colour from the text around it, which is what the spec asks for:
 * "line icon, currentColor, 24x24".
 *
 * Geometry is copied from 03_icons/svg/ unchanged. A handful of glyphs the
 * console needs and the farm app does not — people, key, clock — are drawn
 * here in the same 24×24 / 1.8px round-joined style so the set stays one
 * family.
 */

export type IconName =
  | "arrow-right"
  | "barn"
  | "bell"
  | "calendar"
  | "cart"
  | "chart-line"
  | "check"
  | "chevron-down"
  | "chevron-left"
  | "chevron-right"
  | "clock"
  | "cloud-sync"
  | "coins"
  | "copy"
  | "download"
  | "eye"
  | "inventory"
  | "key"
  | "language"
  | "leaf"
  | "location"
  | "logout"
  | "mail"
  | "money"
  | "package"
  | "people"
  | "plus"
  | "qr"
  | "report"
  | "scale"
  | "search"
  | "settings"
  | "shield"
  | "sun"
  | "task"
  | "user"
  | "warning"
  | "x";

const PATHS: Record<IconName, React.ReactNode> = {
  "arrow-right": <path d="M5 12h14M13 6l6 6-6 6" />,
  barn: (
    <>
      <path d="M3 21V9l9-6 9 6v12" />
      <path d="M8 21v-7h8v7M8 10h8M10 14l6 7M16 14l-6 7" />
    </>
  ),
  bell: (
    <>
      <path d="M18 8a6 6 0 1 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9z" />
      <path d="M10 21h4" />
    </>
  ),
  calendar: <path d="M6 3v4M18 3v4M4 8h16M5 5h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />,
  cart: (
    <>
      <path d="M3 4h2l2.4 10.4a2 2 0 0 0 2 1.6h7.2a2 2 0 0 0 2-1.5L21 8H6" />
      <circle cx="10" cy="20" r="1.3" />
      <circle cx="17" cy="20" r="1.3" />
    </>
  ),
  "chart-line": (
    <>
      <path d="M4 19h16M5 16l4-5 4 3 6-8" />
      <path d="M19 6v5h-5" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  "chevron-down": <path d="m6 9 6 6 6-6" />,
  "chevron-left": <path d="m15 18-6-6 6-6" />,
  "chevron-right": <path d="m9 6 6 6-6 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.5l3.5 2" />
    </>
  ),
  "cloud-sync": (
    <>
      <path d="M7 18H6a4 4 0 1 1 .8-7.9A6 6 0 0 1 18.5 12 3 3 0 1 1 19 18h-2" />
      <path d="M9 16l3-3 3 3M12 13v8" />
    </>
  ),
  coins: (
    <>
      <ellipse cx="8" cy="7" rx="5" ry="3" />
      <path d="M3 7v4c0 1.7 2.2 3 5 3s5-1.3 5-3V7M3 11v4c0 1.7 2.2 3 5 3 1.1 0 2.1-.2 2.9-.5" />
      <ellipse cx="16" cy="15" rx="5" ry="3" />
      <path d="M11 15v4c0 1.7 2.2 3 5 3s5-1.3 5-3v-4" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m0 0 4-4m-4 4-4-4" />
      <path d="M5 21h14" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.8-6 10-6 10 6 10 6-3.8 6-10 6S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  inventory: (
    <>
      <path d="M4 7l8-4 8 4-8 4-8-4z" />
      <path d="M4 7v10l8 4 8-4V7M12 11v10" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="12" r="4" />
      <path d="M12 12h9M18 12v3.5M15.5 12v2.5" />
    </>
  ),
  language: (
    <>
      <path d="M4 5h9M8.5 5v14M5 19c3-3 5-7 6-14" />
      <path d="M14 19l3-8 3 8M15.2 16h3.6" />
    </>
  ),
  leaf: (
    <>
      <path d="M20 4C11 4 5 10 5 19c9 0 15-6 15-15z" />
      <path d="M5 19c3-5 6-8 11-11" />
    </>
  ),
  location: (
    <>
      <path d="M12 21s7-5.3 7-11a7 7 0 0 0-14 0c0 5.7 7 11 7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  logout: (
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5M21 12H9" />
    </>
  ),
  mail: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3.5 7 8.5 6 8.5-6" />
    </>
  ),
  money: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v10M15 9.5c-.8-.7-1.9-1-3-1-1.4 0-2.5.7-2.5 1.8 0 3.1 5 1.2 5 4.2 0 1.1-1.1 2-2.7 2-1.1 0-2.3-.4-3-1.1" />
    </>
  ),
  package: (
    <>
      <path d="m12 2 8 4-8 4-8-4 8-4Z" />
      <path d="M4 6v12l8 4 8-4V6M12 10v12" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="8" r="3.4" />
      <path d="M3 20c0-3.1 2.7-5.2 6-5.2s6 2.1 6 5.2" />
      <path d="M16 5.2a3.4 3.4 0 0 1 0 6.6M17.5 14.4c2.1.6 3.5 2.3 3.5 4.6" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  qr: <path d="M4 4h6v6H4V4zM14 4h6v6h-6V4zM4 14h6v6H4v-6zM14 14h2v2h-2zM18 14h2v6h-6v-2h4zM14 18h2v2h-2z" />,
  report: (
    <>
      <path d="M6 3h9l3 3v15H6V3z" />
      <path d="M15 3v4h4M9 13h6M9 17h6M9 9h2" />
    </>
  ),
  scale: (
    <>
      <path d="M4 20h16M12 4v16M7 20h10M8 7h8" />
      <path d="M6 9l-3 5h6L6 9zM18 9l-3 5h6l-3-5z" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </>
  ),
  settings: (
    <>
      <path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5z" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3.4-.2-.1a1.8 1.8 0 0 0-2.1.1 1.8 1.8 0 0 0-.8 1.8H9.3a1.8 1.8 0 0 0-.8-1.8 1.8 1.8 0 0 0-2.1-.1l-.2.1-2-3.4.1-.1A1.7 1.7 0 0 0 4.6 15a1.8 1.8 0 0 0-1.6-1.1V10a1.8 1.8 0 0 0 1.6-1.1A1.7 1.7 0 0 0 4.3 7l-.1-.1 2-3.4.2.1a1.8 1.8 0 0 0 2.1-.1 1.8 1.8 0 0 0 .8-1.8h5.4a1.8 1.8 0 0 0 .8 1.8 1.8 1.8 0 0 0 2.1.1l.2-.1 2 3.4-.1.1a1.7 1.7 0 0 0-.3 1.9A1.8 1.8 0 0 0 21 10v3.9A1.8 1.8 0 0 0 19.4 15z" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3l7.5 3v6c0 4.4-3.1 8.1-7.5 9.3C7.6 20.1 4.5 16.4 4.5 12V6L12 3z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
    </>
  ),
  task: (
    <>
      <path d="M9 5h10v16H5V5h4z" />
      <path d="M9 5a3 3 0 0 1 6 0M8 12l2 2 4-5M8 18h8" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M5 20c0-3.4 3.1-5.6 7-5.6s7 2.2 7 5.6" />
    </>
  ),
  warning: (
    <>
      <path d="M12 3 22 20H2L12 3z" />
      <path d="M12 9v5M12 17h0" />
    </>
  ),
  x: <path d="M6 6l12 12M18 6L6 18" />,
};

export interface IconProps {
  name: IconName;
  /** Rendered size in px; the set is drawn on a 24px grid. */
  size?: number;
  className?: string;
  /** Only pass this when the icon is the *only* carrier of its meaning. */
  title?: string;
}

/** A 24×24 line icon that inherits the surrounding text colour.
 *
 * Decorative by default — every icon in this console sits beside a label,
 * and announcing both would read the same thing twice. Pass `title` for the
 * rare icon that stands alone (an icon-only button, say).
 */
export function Icon({ name, size = 20, className, title }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/** An icon in a pale semantic roundel, as the KPI cards use it.
 *
 * The spec is explicit that a status tint belongs on the roundel and never
 * across the whole card, so the tone only ever reaches this circle.
 */
export type Tone = "cedar" | "gold" | "danger" | "sky" | "purple" | "neutral";

export function IconRoundel({
  name,
  tone = "cedar",
  size = 44,
}: {
  name: IconName;
  tone?: Tone;
  size?: number;
}) {
  return (
    <span className="roundel" data-tone={tone} style={{ width: size, height: size }}>
      <Icon name={name} size={Math.round(size * 0.5)} />
    </span>
  );
}
