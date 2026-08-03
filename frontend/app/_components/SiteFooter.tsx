import Link from "next/link"

/**
 * There was no footer at all, which meant `/privacy` existed but was reachable
 * only by guessing the URL. A notice nobody can find is not a notice — least of
 * all on the one page where you are about to ask for someone's location.
 *
 * Deliberately not in `layout.tsx`: the live and drive screens are full-bleed
 * and a footer under them would be scroll furniture in the driver's way.
 */
export default function SiteFooter() {
  return (
    <footer className="border-t border-ink-100 bg-white/60">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-6 text-sm text-ink-500">
        <span className="font-display font-semibold text-ink-700">
          EV Trip Optimizer
        </span>
        <Link href="/privacy" className="underline underline-offset-2 hover:text-ink-800">
          Privacy &amp; what we store
        </Link>
        <a
          href="https://github.com/gardan4/EV-holiday-driving-optimiser"
          target="_blank"
          rel="noreferrer noopener"
          className="underline underline-offset-2 hover:text-ink-800"
        >
          Source
        </a>
        <span className="ml-auto text-xs text-ink-400">
          Routes by OpenRouteService · chargers by OpenChargeMap
        </span>
      </div>
    </footer>
  )
}
