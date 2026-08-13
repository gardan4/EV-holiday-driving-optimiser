"use client"

/**
 * The switch that turns usage counting off.
 *
 * Counting runs on legitimate interest rather than consent, and that only holds
 * up if refusing is something an ordinary visitor can actually do. GPC and Do
 * Not Track were the only ways to refuse for a while, and almost nobody knows
 * they exist. This is the version you can find.
 *
 * Mounted-guard on purpose. The state lives in localStorage, which the server
 * cannot see, so rendering the real state on the first pass would make the
 * server HTML and the first client render disagree and break hydration. Until
 * `useEffect` has run we render the neutral "on" wording, which is what is true
 * for almost everyone, and correct it a frame later.
 */

import { useEffect, useState } from "react"
import { countingState, setCounting, type CountingState } from "@/lib/analytics"

export default function CountingToggle() {
  const [state, setState] = useState<CountingState | null>(null)

  useEffect(() => {
    setState(countingState())
  }, [])

  // The browser said no before we got a say. A switch here would either lie
  // about what it does or quietly override a setting somebody chose on purpose.
  if (state === "browser") {
    return (
      <span className="mt-3 inline-flex items-center gap-2 rounded-xl border border-brand-200 bg-brand-50 px-3 py-2 text-sm text-brand-700">
        Your browser is already asking sites not to count you, so we don&apos;t.
      </span>
    )
  }

  const on = state !== "off"

  return (
    <span className="mt-3 flex flex-wrap items-center gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="Count my visits"
        disabled={state === null}
        onClick={() => {
          const next = !on
          setCounting(next)
          setState(next ? "on" : "off")
        }}
        className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
          on ? "bg-brand-500" : "bg-ink-300"
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-[left] ${
            on ? "left-[1.375rem]" : "left-0.5"
          }`}
        />
      </button>
      <span className="text-sm text-ink-600">
        {on ? (
          "Counting my visits. Switch this off and the random number is deleted straight away."
        ) : (
          <strong className="font-semibold text-ink-800">
            Off. The random number has been deleted and nothing about your visits
            is being recorded.
          </strong>
        )}
      </span>
    </span>
  )
}
