import type { MetadataRoute } from "next"

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "EV Trip Optimizer",
    short_name: "EV Trip",
    description:
      "Find the cruise speed that gets you there earliest in an EV, charging stops included, then follow the drive as it happens.",
    start_url: "/",
    display: "standalone",
    background_color: "#f8fafc",
    // The brand's EV-mint, not the starter template's blue. Add-to-home-screen
    // is the cheapest way to make a phone the driving device, so the icon and
    // splash should look like this app.
    theme_color: "#17a56b",
    icons: [{ src: "/favicon.ico", sizes: "any", type: "image/x-icon" }],
  }
}
