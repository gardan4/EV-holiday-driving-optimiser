import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import { Providers } from "./providers";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.app";

const TITLE = "EV Trip Optimizer";
const DESCRIPTION = "EV Trip Optimizer — a full-stack starter (FastAPI + Next.js + Clerk on Azure).";

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
    <ClerkProvider
      // Custom in-app auth pages. These MUST be declared explicitly: without
      // them Clerk falls back to the instance default sign-in URL, which on a
      // *production* instance is the hosted Account Portal — so <SignIn> mounted
      // at /login never renders and the page hangs. (Dev/test instances render
      // inline regardless, which is why dev can work while prod hangs.)
      signInUrl="/login"
      signUpUrl="/register"
      signInFallbackRedirectUrl="/dashboard"
      signUpFallbackRedirectUrl="/dashboard"
      afterSignOutUrl="/login"
      appearance={{ variables: { colorPrimary: "#0C6291", borderRadius: "0.75rem" } }}
    >
      <html lang="en">
        <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
          <Providers>{children}</Providers>
        </body>
      </html>
    </ClerkProvider>
  );
}
