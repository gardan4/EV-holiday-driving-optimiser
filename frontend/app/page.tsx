import SiteFooter from "./_components/SiteFooter"
import TripForm from "./_components/trip/TripForm"

/** Public landing page — hero + the planner form. */
export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden">
      {/* Soft alpine backdrop */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden>
        <div className="absolute -top-32 -left-32 h-96 w-96 rounded-full bg-brand-200/40 blur-3xl animate-float" />
        <div className="absolute -bottom-32 right-0 h-80 w-80 rounded-full bg-sky-200/40 blur-3xl animate-float" />
        <MountainSilhouette />
      </div>

      <section className="relative z-10 mx-auto max-w-5xl px-4 pb-16 pt-14 sm:px-6 sm:pt-20">
        <div className="mx-auto max-w-2xl text-center">
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-brand-600">
            EV road-trip speed optimizer
          </p>
          <h1 className="mt-4 font-display text-4xl font-bold tracking-tight text-ink-900 sm:text-6xl">
            Should you really
            <br />
            drive <span className="text-brand-500">100</span>?
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-ink-500 sm:text-lg">
            Faster driving means more charging — but slower isn&apos;t always sooner.
            We simulate your route at every cruise speed, charging curve and all,
            and find the one that gets you there first.
          </p>
        </div>

        <div className="mx-auto mt-10 max-w-2xl animate-fade-in-up">
          <TripForm />
        </div>

        <p className="mx-auto mt-8 max-w-xl text-center text-xs leading-relaxed text-ink-400">
          Assumes motorway cruise at your chosen speed where legally possible (130 in
          AT/NL, derestricted where German autobahn allows), real fast-chargers along
          the route, and your car&apos;s measured charging curve. Night-owl approved.
        </p>
      </section>

      <div className="relative z-10">
        <SiteFooter />
      </div>
    </main>
  )
}

/** Flat-shaded low-poly mountain line along the bottom — a hint of the diorama. */
function MountainSilhouette() {
  return (
    <svg
      className="absolute bottom-0 left-0 w-full text-brand-100"
      viewBox="0 0 1440 220"
      preserveAspectRatio="none"
      fill="none"
    >
      <path
        d="M0 220 L120 140 L240 190 L400 90 L540 170 L720 60 L900 160 L1060 100 L1200 180 L1320 130 L1440 200 L1440 220 Z"
        fill="currentColor"
      />
      <path
        d="M0 220 L180 170 L360 205 L560 140 L760 200 L980 150 L1180 205 L1440 165 L1440 220 Z"
        fill="#dfe9e4"
      />
    </svg>
  )
}
