"use client"

/**
 * The erase button the privacy page promises.
 *
 * With no accounts there is nobody to look up, so the honest answer to "delete
 * my data" used to be "email someone and hope" — which is not a right anyone
 * can exercise. The trip link is the only key this app has, so holding it is
 * what authorises the deletion.
 *
 * Two-step on purpose. This is the one irreversible control in the app, it sits
 * next to Share, and a trip link is often the only copy of a plan a whole car
 * full of people are relying on.
 *
 * The confirmation is a DIALOG rather than an inline panel. It used to expand
 * in place inside the results header's action row — a row that does not wrap —
 * so on a phone the warning squeezed "Drive this", "Share" and "New trip" into
 * a few characters each and wrapped itself over five lines. A question this
 * consequential should not be asked in whatever space happens to be left over.
 *
 * Used in two places, hence the props: the results page (labelled trigger,
 * navigates home afterwards) and the trips drawer (icon-only trigger, tells the
 * list to drop the row).
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Loader2, Trash2 } from "lucide-react"
import { deleteTrip } from "@/lib/client"
import { useOverlayPresence } from "@/lib/overlay"

export default function DeleteTrip({
  tripId,
  compact = false,
  onDeleted,
}: {
  tripId: string
  /** Icon-only trigger, for a list row rather than a page header. */
  compact?: boolean
  /** Defaults to navigating home, which is right when the page you are on has
   *  just ceased to exist and wrong when it is one row of a list. */
  onDeleted?: () => void
}) {
  const router = useRouter()
  const [arming, setArming] = useState(false)
  // 260ms, the sheet's travel on a phone — the longer of the two exits in
  // `globals.css`, so it is the one that decides when this may unmount.
  const dialog = useOverlayPresence(arming, 260)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Escape closes it, and the destructive button is never the default action of
  // a stray Enter — the dialog opens with nothing focused.
  useEffect(() => {
    if (!arming) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) setArming(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [arming, busy])

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await deleteTrip(tripId)
      if (onDeleted) {
        onDeleted()
        setArming(false)
      } else {
        router.push("/")
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this trip")
      setBusy(false)
    }
  }

  return (
    <>
      <button
        onClick={(e) => {
          // In the drawer this sits on top of a link to the trip; without this
          // the confirmation and the navigation both happen.
          e.preventDefault()
          e.stopPropagation()
          setArming(true)
        }}
        title="Delete this trip and any drives on it"
        aria-label="Delete this trip"
        className={
          compact
            ? "rounded-lg p-1.5 text-ink-300 transition-colors hover:bg-red-50 hover:text-red-600"
            : "inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm font-medium text-ink-500 transition-colors hover:border-red-300 hover:text-red-700"
        }
      >
        <Trash2 className="h-4 w-4" />
        {!compact && <span className="sr-only sm:not-sr-only">Delete</span>}
      </button>

      {dialog.present && (
        <div
          data-leaving={dialog.leaving}
          className="overlay-scrim fixed inset-0 z-[60] flex items-end justify-center bg-ink-900/40 p-4 backdrop-blur-[2px] sm:items-center"
          onClick={() => !busy && setArming(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Delete this trip"
        >
          {/* Bottom sheet on a phone, centred card above it — a destructive
              confirmation should land under the thumb, not at the top of the
              screen. */}
          <div
            data-leaving={dialog.leaving}
            className="overlay-sheet w-full max-w-sm rounded-2xl border border-ink-100 bg-white p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-display text-lg font-semibold text-ink-900">
              Delete this trip?
            </h2>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-600">
              This removes the plan, any drives recorded on it and their location
              trails. The link stops working for everyone who has it. It cannot be
              undone.
            </p>
            {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
            {/* py-3 for a 44px target: this is the one irreversible control in
                the app and the two buttons sit side by side under a thumb. */}
            <div className="mt-4 flex justify-end gap-2 [&>*]:py-3">
              <button
                onClick={() => setArming(false)}
                disabled={busy}
                className="rounded-xl px-3.5 py-2 text-sm font-medium text-ink-600 transition-[transform,background-color] duration-150 ease-out hover:bg-ink-50 active:scale-[0.97] disabled:opacity-60 disabled:active:scale-100"
              >
                Keep it
              </button>
              <button
                onClick={() => void confirm()}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-xl bg-red-600 px-3.5 py-2 text-sm font-semibold text-white transition-[transform,background-color] duration-150 ease-out hover:bg-red-700 active:scale-[0.97] disabled:opacity-60 disabled:active:scale-100"
              >
                {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
