import Link from "next/link"
import JsonLd from "./_components/JsonLd"
import SiteFooter from "./_components/SiteFooter"
import TripForm from "./_components/trip/TripForm"
import TripsDrawer from "./_components/trips/TripsDrawer"
import { FAQ } from "@/lib/faq"
import { faqLd, graph, organizationLd, webApplicationLd, websiteLd } from "@/lib/seo"

/** Public landing page — hero, the planner form, and the explanatory copy that
 *  gives search engines and assistants something to read. The hero headline is
 *  a brand line ("Should you really drive 100?") and matches no query anyone
 *  types, so the sections below carry the actual subject matter. */
export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden">
      <JsonLd
        data={graph(organizationLd(), websiteLd(), webApplicationLd(), faqLd())}
      />

      {/* The list of what you already planned, right where you would otherwise
          plan it again. */}
      <TripsDrawer />

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
            Faster driving means more charging, but slower isn&apos;t always sooner.
            We simulate your route at every cruise speed, charging curve and all,
            and find the one that gets you there first.
          </p>
        </div>

        <div className="mx-auto mt-10 max-w-2xl animate-fade-in-up">
          <TripForm />
        </div>

      </section>

      {/* Opaque, not the `bg-white/70 backdrop-blur` the hero cards use. Frosted
          glass suits a card floating over the hero; this is ~2000px of body
          text, and the fixed mint/sky blobs drifting behind it would sit under
          every paragraph for the whole scroll. Solid surface, constant
          contrast. */}
      <section className="relative z-10 border-t border-ink-100 bg-white">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
          <h2 className="font-display text-2xl font-bold tracking-tight text-ink-900 sm:text-3xl">
            What speed should you drive an EV on a long trip?
          </h2>
          <p className="mt-4 text-base leading-relaxed text-ink-500">
            Aerodynamic drag rises with the square of speed, so 130 km/h uses roughly a quarter
            more energy per kilometre than 100 does. That energy has to come back at a charger, and
            charging power tapers steeply as the battery fills, so the time you save on the road can
            be handed straight back at the plug. Somewhere between those two effects sits the speed
            that gets you there earliest.
          </p>
          <p className="mt-4 text-base leading-relaxed text-ink-500">
            It is usually higher than people expect. On a real 956 km run from Utrecht to Innsbruck
            in a Cupra Born 58, driving 130 took 10h41 and the quickest speed of all, 145, took
            10h36. Five minutes for fifteen more km/h. Drop to 100 and the same trip takes 11h40.
            The penalty for crawling is far bigger than the prize for pushing on, which is roughly
            the opposite of the usual advice.
          </p>
          <p className="mt-4 text-base leading-relaxed text-ink-500">
            That is one route in one car, though, and yours will differ. Where the fast chargers sit
            on your road matters as much as the car does. So the app works it out for the actual
            trip: it fetches your real route, simulates it at every cruise speed from 90 to 160 km/h
            with a separate charging plan for each, and shows you the total journey time for all of
            them.
          </p>

          <h2 className="mt-14 font-display text-2xl font-bold tracking-tight text-ink-900 sm:text-3xl">
            How it works
          </h2>
          <ol className="mt-6 space-y-5">
            <Step n={1} title="The real route, not a straight line">
              Your origin and destination are geocoded and routed with OpenRouteService, giving the
              genuine road distance, the elevation profile and the free-flow speed of every segment.
              Speed limits are applied per country, including the German autobahn sections that are
              actually derestricted.
            </Step>
            <Step n={2} title="Real chargers, your car's real curve">
              Fast chargers along the corridor come from OpenChargeMap. Your car brings a
              hand-curated DC charging curve and a consumption model fitted to motorway speeds, so a
              stop costs what it would really cost rather than a flat average.
            </Step>
            <Step n={3} title="Every speed, every stop plan">
              For each cruise speed a dynamic program searches every combination of charger and
              charge level and returns the fastest one. Nothing tells it to &ldquo;arrive low and
              charge to 70%&rdquo;. That behaviour falls out of the search on its own, which is how
              you know it is the maths and not a rule of thumb.
            </Step>
          </ol>

          <div className="mt-10 rounded-2xl border border-brand-200 bg-brand-50 p-5">
            <h3 className="font-display text-lg font-semibold text-ink-900">
              Charging curves for every car in the catalog
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-600">
              Usable battery, peak DC power, modelled 10-80% time and motorway consumption at each
              cruise speed, for every electric car the planner can simulate.
            </p>
            <Link
              href="/ev"
              className="mt-3 inline-block font-semibold text-brand-700 underline underline-offset-4 hover:text-brand-800"
            >
              Browse the EV catalog →
            </Link>
          </div>

          <h2 className="mt-14 font-display text-2xl font-bold tracking-tight text-ink-900 sm:text-3xl">
            Common questions
          </h2>
          <dl className="mt-6 divide-y divide-ink-100 border-t border-ink-100">
            {FAQ.map((item) => (
              <div key={item.q} className="py-5">
                <dt className="font-display text-base font-semibold text-ink-900">{item.q}</dt>
                <dd className="mt-2 text-sm leading-relaxed text-ink-500">{item.a}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <div className="relative z-10">
        <SiteFooter />
      </div>
    </main>
  )
}

function Step({
  n,
  title,
  children,
}: {
  n: number
  title: string
  children: React.ReactNode
}) {
  return (
    <li className="flex gap-4">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-100 font-display text-sm font-bold text-brand-700">
        {n}
      </span>
      <div>
        <h3 className="font-display text-base font-semibold text-ink-900">{title}</h3>
        <p className="mt-1 text-sm leading-relaxed text-ink-500">{children}</p>
      </div>
    </li>
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
