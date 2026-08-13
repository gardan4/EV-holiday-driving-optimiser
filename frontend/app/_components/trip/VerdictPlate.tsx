"use client"

import { ReactNode } from "react"

/** What the hero needs to state the answer; built by ResultsView. */
export interface HeroVerdict {
  headline: ReactNode
  sub?: string
}

/**
 * The one-sentence answer, anchored on the journey scene so a fresh visitor
 * gets it without scrolling. Purely presentational — JourneyHero decides when
 * it is visible (at rest at the start; playback and the finish banner own the
 * other moments).
 */
export default function VerdictPlate({
  verdict,
  visible,
}: {
  verdict: HeroVerdict
  visible: boolean
}) {
  return (
    <div
      aria-hidden={!visible}
      className={`pointer-events-none max-w-[calc(100vw-6.5rem)] rounded-xl border border-white/12 bg-black/35 px-3 py-2 backdrop-blur transition-opacity duration-300 sm:max-w-md ${
        visible ? "opacity-100" : "opacity-0"
      }`}
    >
      <div className="font-display text-lg font-bold leading-tight text-white sm:text-xl">
        {verdict.headline}
      </div>
      {verdict.sub && (
        <div className="mt-1 font-mono text-[11px] text-white/50">{verdict.sub}</div>
      )}
    </div>
  )
}
