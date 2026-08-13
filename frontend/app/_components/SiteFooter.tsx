import Link from "next/link"
import CountingLink from "./CountingLink"
import FeedbackLink from "./FeedbackLink"

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
      {/* Extra room at the bottom on a phone: the floating feedback button is
          pinned to the viewport's bottom-left, which is exactly where this row
          ends up once you've scrolled to the end of the page. */}
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-2 px-4 pb-24 pt-6 text-sm text-ink-500 sm:pb-6">
        <span className="font-display font-semibold text-ink-700">
          EV Trip Optimizer
        </span>
        <Link href="/ev" className="underline underline-offset-2 hover:text-ink-800">
          EV charging curves
        </Link>
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
        <FeedbackLink />
        <CountingLink />
        {/* ODbL asks for OpenStreetMap contributors by name, not just for the
            router built on top of them. OpenChargeMap asks for the same. */}
        <span className="ml-auto text-xs text-ink-400">
          Routes by OpenRouteService, from{" "}
          <a
            href="https://www.openstreetmap.org/copyright"
            target="_blank"
            rel="noreferrer noopener"
            className="underline underline-offset-2 hover:text-ink-600"
          >
            © OpenStreetMap contributors
          </a>{" "}
          · chargers by{" "}
          <a
            href="https://openchargemap.org/"
            target="_blank"
            rel="noreferrer noopener"
            className="underline underline-offset-2 hover:text-ink-600"
          >
            OpenChargeMap contributors
          </a></span>
      </div>
    </footer>
  )
}
