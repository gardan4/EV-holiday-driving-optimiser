import type { MetadataRoute } from "next"

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "EV Trip Optimizer",
    short_name: "EV Trip Optimizer",
    description: "EV Trip Optimizer — a full-stack starter.",
    start_url: "/",
    display: "standalone",
    background_color: "#f8fafc",
    theme_color: "#0C6291",
    icons: [{ src: "/favicon.ico", sizes: "any", type: "image/x-icon" }],
  }
}
