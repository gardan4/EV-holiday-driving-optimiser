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
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Loader2, Trash2 } from "lucide-react"
import { deleteTrip } from "@/lib/client"

export default function DeleteTrip({ tripId }: { tripId: string }) {
  const router = useRouter()
  const [arming, setArming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await deleteTrip(tripId)
      router.push("/")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this trip")
      setBusy(false)
    }
  }

  if (!arming) {
    return (
      <button
        onClick={() => setArming(true)}
        title="Delete this trip and any drives on it"
        aria-label="Delete this trip"
        className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm font-medium text-ink-500 transition-colors hover:border-red-300 hover:text-red-700"
      >
        <Trash2 className="h-4 w-4" />
        <span className="sr-only sm:not-sr-only">Delete</span>
      </button>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2">
      <span className="text-sm text-red-900">
        {error ?? "Delete this trip and any drives on it? This cannot be undone."}
      </span>
      <button
        onClick={() => void confirm()}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-60"
      >
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        Delete for good
      </button>
      <button
        onClick={() => {
          setArming(false)
          setError(null)
        }}
        className="px-2 py-1.5 text-sm text-ink-600 underline underline-offset-2"
      >
        Keep it
      </button>
    </div>
  )
}
