import type { Metadata, Viewport } from "next";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Origami Server Admin",
  description: "Platform control console for Origami FarmOS",
  manifest: "/site.webmanifest",
  icons: {
    icon: [
      { url: "/icons/origami_icon_32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/origami_icon_192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: { url: "/icons/origami_icon_180.png", sizes: "180x180", type: "image/png" },
  },
};

export const viewport: Viewport = {
  // Cedar, so the browser chrome on a phone or an installed window carries
  // the brand rather than a default grey band above it.
  themeColor: "#2F6B4F",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
