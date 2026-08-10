import { ImageResponse } from "next/og"

/**
 * Default social-share card (1200×630) rendered by next/og. Self-contained — no
 * external assets or fonts — so link previews render a branded card instead of a
 * bare URL. Individual routes can override with their own opengraph-image.
 */

export const alt = "EV Trip Optimizer"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

const BRAND = "#0C6291" // brand-500

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 24,
          background: `linear-gradient(135deg, #06263a 0%, ${BRAND} 60%, #3b1d6e 130%)`,
          padding: "72px 80px",
          color: "white",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ fontSize: 30, fontWeight: 600, letterSpacing: "-0.01em" }}>
          EV Trip Optimizer
        </div>
        <div style={{ fontSize: 64, fontWeight: 700, lineHeight: 1.05, letterSpacing: "-0.02em", maxWidth: 900 }}>
          Should you really drive 100?
        </div>
        <div style={{ fontSize: 28, lineHeight: 1.35, color: "rgba(255,255,255,0.82)", maxWidth: 880 }}>
          Find the cruise speed that gets your EV there first, charging stops included.
        </div>
      </div>
    ),
    { ...size },
  )
}
