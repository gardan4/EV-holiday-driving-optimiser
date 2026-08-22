"use client"

import { useEffect, useState } from "react"
import { Ban } from "lucide-react"
import { getNetworks, Network } from "@/lib/client"
import { InfoTip } from "./fields"

/**
 * "Not Ionity." Networks the plan may not stop at.
 *
 * Chips rather than a multi-select, because the whole interaction is "tap the
 * two you never use and never think about it again" — and unlike the number
 * fields on this form, the state is legible at a glance without opening
 * anything: a struck-through chip is off.
 *
 * The list comes from the SERVER (`GET /api/networks`). A copy in here would go
 * on offering a network the matcher had renamed, and the toggle would do
 * nothing with no error anywhere — the plan it produced would be a perfectly
 * good plan, just not the one that was asked for.
 *
 * A failed fetch renders NOTHING, deliberately. This is a preference on a form
 * that works without it; a row of dead chips, or an error about charging
 * networks above the departure time, is worse than the feature quietly not
 * being offered on the one load where the request fell over.
 */
export default function NetworkPicker({
  value,
  onChange,
  label = "Networks to avoid",
}: {
  value: string[]
  onChange: (slugs: string[]) => void
  label?: string
}) {
  const [networks, setNetworks] = useState<Network[]>([])

  useEffect(() => {
    let alive = true
    getNetworks()
      .then((n) => {
        if (alive) setNetworks(n)
      })
      .catch(() => {
        /* see the note above — the form is fine without this */
      })
    return () => {
      alive = false
    }
  }, [])

  // A trip planned before a slug was renamed still carries the old one. It
  // cannot be rendered as a chip (there is no label for it) and it must not be
  // silently dropped from the request either, so it rides along untouched and
  // the count below says how many are off in total.
  const known = new Set(networks.map((n) => n.slug))
  const unknown = value.filter((s) => !known.has(s))

  if (networks.length === 0) return null

  function toggle(slug: string) {
    // Sorted, matching what the server stores. The assumptions panel decides
    // "has anything changed" by comparing the whole request as JSON, so a list
    // that depended on tap order would report a re-plan as needed after
    // switching one network off and straight back on.
    onChange(
      (value.includes(slug)
        ? value.filter((s) => s !== slug)
        : [...value, slug]
      ).sort()
    )
  }

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-xs font-semibold uppercase tracking-wider text-ink-500">
          {label}
        </span>
        <InfoTip>
          {value.length > 0 && (
            <span className="mb-1.5 block font-semibold text-ink-900">
              {value.length} avoided — the plan will drive past them
            </span>
          )}
          Tap a network you can&apos;t or won&apos;t use — no subscription, a card
          that has never worked, a price you&apos;d rather not pay. The plan then
          stops somewhere else, which usually costs a few minutes and occasionally
          costs a lot; the answer tells you which. It only changes where the plan
          SENDS you: if you end up charging at one anyway, the drive still knows
          where you are.
        </InfoTip>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {networks.map((n) => {
          const off = value.includes(n.slug)
          return (
            <button
              key={n.slug}
              type="button"
              onClick={() => toggle(n.slug)}
              aria-pressed={off}
              className={`flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                off
                  ? "border-red-300 bg-red-50 text-red-800 line-through decoration-red-400"
                  : "border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:bg-ink-100"
              }`}
            >
              {off && <Ban className="h-3 w-3 shrink-0 no-underline" />}
              {n.label}
            </button>
          )
        })}
      </div>
      {unknown.length > 0 && (
        <p className="mt-1.5 text-xs text-ink-400">
          Plus {unknown.length} this plan was made with that we no longer list.
        </p>
      )}
    </div>
  )
}
