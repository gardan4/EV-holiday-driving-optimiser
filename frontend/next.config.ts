import type { NextConfig } from "next";

// NOTE: security headers + CSP live in proxy.ts (single source of truth,
// request-aware, and already wired for Clerk's domains). Don't add a headers()
// block here too — two CSP sources fight each other.
const nextConfig: NextConfig = {
  // Standalone output → a self-contained server bundle the Dockerfile copies
  // into a slim runtime image (see frontend/Dockerfile). Required for the
  // container-on-App-Service deploy.
  output: "standalone",

  // Image optimization needs a running Node optimizer; disable it so static
  // export / CDN fronting works without one. Re-enable if you add a loader.
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
