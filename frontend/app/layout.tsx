import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Geist, Geist_Mono } from "next/font/google";
import { Providers } from "./providers";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const display = Bricolage_Grotesque({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
});

// Fallback only. `NEXT_PUBLIC_SITE_URL` is set at build time by the deploy
// workflow; this is what you get if that ever goes missing again. It has to be
// a host we actually serve — the previous placeholder was a domain registered
// to somebody else, so every og:image and canonical URL pointed at a stranger.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip-web-dev.azurewebsites.net";

const TITLE = "EV Trip Optimizer — find your fastest cruise speed";
const DESCRIPTION =
  "Should you really drive 100? Enter your route and EV, and find the cruise speed that gets you there first — charging stops included.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: TITLE, template: "%s · EV Trip Optimizer" },
  description: DESCRIPTION,
  applicationName: "EV Trip Optimizer",
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "EV Trip Optimizer",
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
  robots: { index: true, follow: true },
  icons: { icon: [{ url: "/favicon.ico", sizes: "any" }], shortcut: "/favicon.ico" },
};

export const viewport: Viewport = {
  themeColor: "#0C6291", // brand-500
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} ${display.variable} antialiased`}>
        {process.env.NODE_ENV === "development" && (
          // Dev-only: headless/embedded preview panes report visibilityState
          // "hidden", which parks requestAnimationFrame and freezes the 3D
          // scene. Shim RAF with a timer so the render loop still runs there.
          <script
            dangerouslySetInnerHTML={{
              __html: `if (document.visibilityState === "hidden") {
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 16);
  window.cancelAnimationFrame = (id) => clearTimeout(id);
}`,
            }}
          />
        )}
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
