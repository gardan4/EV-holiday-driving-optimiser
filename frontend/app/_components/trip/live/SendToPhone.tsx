"use client"

/**
 * Getting the plan onto the phone that's actually coming along.
 *
 * This replaced a "Start this drive" button on the results page, and the change
 * is the whole point: the driver token is created wherever the button is
 * pressed, and a laptop on Tuesday is never the right device. Pressing it there
 * started the clock days early, asked a desktop for a GPS fix, and left the
 * phone a permanent spectator of its own journey.
 *
 * So nothing is started here. This hands the trip to a phone, and the phone
 * starts the drive — see `/trip/[id]/drive`.
 *
 * Unless you are already on one. A touch device can be the driving device, and
 * showing it a QR code it would have to scan with itself is a small absurdity,
 * so it skips straight to the start screen. The decision is made on click
 * rather than at render, so there's no hydration mismatch and no flash of the
 * wrong label.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Check, Link2, Loader2, Share2, Smartphone } from "lucide-react"

/** Can this device plausibly be the one in the car? A coarse pointer means a
 *  touchscreen, which is the closest thing to an honest signal for "phone". */
function isHandheld(): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia("(pointer: coarse)").matches
}

/**
 * The drive URL for whichever plan the reader is looking at.
 *
 * The results page has always let you choose a speed other than the optimum,
 * and "Drive this" has always ignored that and started the optimum — which was
 * survivable while the choice was a row of pills below the fold, and stopped
 * being once the steppers put it next to the answer. Somebody who dials to 130
 * and hands the trip to their phone would otherwise be followed against, and
 * benchmarked against, the 145 plan they deliberately rejected.
 *
 * Omitted when it IS the optimum, so the ordinary link stays clean and the QR
 * for an untouched plan is the same code it has always been.
 */
export function driveHref(tripId: string, speedKph?: number | null): string {
  const base = `/trip/${tripId}/drive`
  return speedKph == null ? base : `${base}?speed=${Math.round(speedKph)}`
}

export function DriveThisButton({
  tripId,
  speedKph = null,
  onShowQr,
}: {
  tripId: string
  /** The speed the reader has selected, when it isn't the optimum. */
  speedKph?: number | null
  onShowQr: () => void
}) {
  const router = useRouter()
  return (
    <button
      onClick={() =>
        isHandheld() ? router.push(driveHref(tripId, speedKph)) : onShowQr()
      }
      className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700"
    >
      <Smartphone className="h-4 w-4" />
      Drive this
    </button>
  )
}

/**
 * The hand-off card. Rendered full-width BELOW the header rather than inside
 * its button row — as a flex sibling of Share and New trip it collapsed into a
 * narrow column on a phone, with the QR squeezed against the buttons.
 */
export function SendToPhonePanel({
  tripId,
  speedKph = null,
  onClose,
}: {
  tripId: string
  /** The speed the reader has selected, when it isn't the optimum. The QR
   *  re-encodes when it changes: the code on screen has to be the plan on
   *  screen, or the phone quietly drives a different one. */
  speedKph?: number | null
  onClose: () => void
}) {
  const [svg, setSvg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [url, setUrl] = useState("")

  useEffect(() => {
    const target = `${window.location.origin}${driveHref(tripId, speedKph)}`
    setUrl(target)
    let alive = true
    void import("qrcode").then((QR) =>
      QR.toString(target, {
        type: "svg",
        margin: 1,
        width: 190,
        color: { dark: "#0f172a", light: "#ffffff" },
      }).then((s) => {
        if (alive) setSvg(s)
      })
    )
    return () => {
      alive = false
    }
  }, [tripId, speedKph])

  async function copy() {
    await navigator.clipboard.writeText(url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  async function share() {
    try {
      await navigator.share({
        title: "Drive this trip",
        text: "Open this on the phone that's coming along.",
        url,
      })
    } catch {
      /* dismissed — the QR and the copy button are still right there */
    }
  }

  return (
    <div className="mb-6 rounded-2xl border border-brand-200 bg-brand-50/60 p-4 sm:p-5">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <div className="grid h-[190px] w-[190px] shrink-0 place-items-center self-center rounded-xl bg-white p-2 shadow-sm sm:self-auto">
          {svg ? (
            // Inline SVG from a local encoder — no image request, no origin.
            <div
              className="[&>svg]:h-full [&>svg]:w-full"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : (
            <Loader2 className="h-5 w-5 animate-spin text-ink-300" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <h3 className="font-display text-base font-semibold text-ink-900">
            Take it with you
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-ink-600">
            Scan this with the phone that&apos;s coming along. That phone starts
            the drive and follows it. Everyone else you send the link to can
            watch, but only the phone that starts it can steer.
          </p>
          <p className="mt-2 text-xs leading-relaxed text-ink-500">
            Nothing starts until you press go on the phone, so scanning this now
            is safe.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {typeof navigator !== "undefined" && "share" in navigator && (
              <button
                onClick={() => void share()}
                className="inline-flex items-center gap-1.5 rounded-xl bg-ink-900 px-3 py-2 text-sm font-semibold text-white"
              >
                <Share2 className="h-4 w-4" />
                Send to phone
              </button>
            )}
            <button
              onClick={() => void copy()}
              className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm font-medium text-ink-700"
            >
              {copied ? (
                <Check className="h-4 w-4 text-brand-600" />
              ) : (
                <Link2 className="h-4 w-4" />
              )}
              {copied ? "Copied" : "Copy driver link"}
            </button>
            <button
              onClick={onClose}
              className="px-2 py-2 text-sm text-ink-500 underline underline-offset-2"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
