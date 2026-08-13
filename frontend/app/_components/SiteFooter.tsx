import Link from "next/link"
import CountingLink from "./CountingLink"
import FeedbackLink from "./FeedbackLink"

/**
 * There was no footer at all, which meant `/privacy` existed but was reachable
 * only by guessing the URL. A notice nobody can find is not a notice — least of
 * all on the one page where you are about to ask for someone's location.
 *
 * Two tiers rather than one wrapping row. Everything used to sit on a single
 * flex line: the wordmark, the links, a feedback chip, a counting status, and
 * an attribution string long enough to wrap onto a second line and shove the
 * rest around. Six unrelated things at one visual weight reads as a pile.
 * Navigation is now its own row, and the legal small print sits below a rule at
 * a smaller size, which is the shape it actually has.
 *
 * `FeedbackLink` renders only the floating button. Its inline copy lived here
 * and was pure duplication once the floating one existed, since that one is
 * fixed and therefore already on screen when you reach this.
 *
 * Deliberately not in `layout.tsx`: the live and drive screens are full-bleed
 * and a footer under them would be scroll furniture in the driver's way.
 */
export default function SiteFooter() {
  return (
    <footer className="border-t border-ink-100 bg-white/60">
      {/* Extra room at the bottom on a phone: the floating feedback button is
          pinned to the viewport's bottom-left, which is exactly where this
          lands once you've scrolled to the end of the page. */}
      <div className="mx-auto max-w-5xl px-4 pb-24 pt-10 sm:pb-8">
        <div className="flex flex-col gap-7 sm:flex-row sm:items-start sm:justify-between sm:gap-10">
          <div>
            <span className="font-display text-base font-semibold text-ink-800">
              EV Trip Optimizer
            </span>
            <p className="mt-1.5 max-w-xs text-sm leading-relaxed text-ink-400">
              The cruise speed that gets you there first, charging stops
              included.
            </p>
          </div>

          <nav
            aria-label="Footer"
            className="flex flex-wrap items-center gap-x-6 gap-y-2.5 text-sm text-ink-600 sm:justify-end"
          >
            <Link
              href="/ev"
              className="transition-colors hover:text-ink-900 hover:underline hover:underline-offset-4"
            >
              EV charging curves
            </Link>
            <Link
              href="/privacy"
              className="transition-colors hover:text-ink-900 hover:underline hover:underline-offset-4"
            >
              Privacy
            </Link>
            <a
              href="https://github.com/gardan4/EV-holiday-driving-optimiser"
              target="_blank"
              rel="noreferrer noopener"
              className="transition-colors hover:text-ink-900 hover:underline hover:underline-offset-4"
            >
              Source
            </a>
          </nav>
        </div>

        <div className="mt-8 flex flex-col-reverse gap-4 border-t border-ink-100 pt-5 sm:flex-row sm:items-center sm:justify-between">
          {/* ODbL asks for OpenStreetMap contributors by name, not just for the
              router built on top of them. OpenChargeMap asks for the same. */}
          <p className="text-xs leading-relaxed text-ink-400">
            Routes by OpenRouteService, from{" "}
            <a
              href="https://www.openstreetmap.org/copyright"
              target="_blank"
              rel="noreferrer noopener"
              className="underline underline-offset-2 hover:text-ink-600"
            >
              © OpenStreetMap contributors
            </a>
            . Chargers by{" "}
            <a
              href="https://openchargemap.org/"
              target="_blank"
              rel="noreferrer noopener"
              className="underline underline-offset-2 hover:text-ink-600"
            >
              OpenChargeMap contributors
            </a>
            .
          </p>
          <CountingLink />
        </div>
      </div>
      <FeedbackLink />
    </footer>
  )
}
