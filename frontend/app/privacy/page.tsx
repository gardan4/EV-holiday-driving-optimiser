import type { Metadata } from "next"
import Link from "next/link"
import AppHeader from "@/app/_components/AppHeader"

export const metadata: Metadata = {
  title: "Privacy",
  description: "What this app stores, and for how long.",
}

/**
 * Written because live mode started storing location traces, and a route
 * between two places one of which is usually "home" is the kind of data that
 * deserves a straight answer rather than a policy.
 */
export default function PrivacyPage() {
  return (
    <main className="min-h-screen">
      <AppHeader />
      <div className="mx-auto max-w-2xl px-4 pb-20 pt-8">
        <h1 className="font-display text-3xl font-bold text-ink-900">Privacy</h1>
        <p className="mt-2 text-sm text-ink-500">
          Short version: there are no accounts, we don&apos;t know who you are,
          and location data is deleted after 90 days.
        </p>

        <Section title="There is no sign-in">
          This app has no accounts, no user table and no email addresses. We
          have no identifier for you, so nothing here is tied to a person — only
          to a trip.
        </Section>

        <Section title="Trips are unlisted, not private">
          Planning a trip stores your start, destination, car, departure time
          and the settings you chose, under a long random link. Anyone with that
          link can see the trip, so treat it like a password: share it with the
          people coming with you, not publicly. Trip pages are excluded from
          search engines.
        </Section>

        <Section title="Following a drive stores where you were">
          If you start a drive, the phone doing the driving sends its position
          every 25 seconds while the tab is open. We keep a coarse trail — a
          point every five minutes or so — plus the battery readings you type
          in, so the app can tell you whether you&apos;re ahead or behind and
          show you afterwards how it went.
          <strong className="block pt-2 font-semibold text-ink-800">
            That trail is a record of a journey you actually made. Anyone with
            the trip link can watch it live and read it afterwards.
          </strong>
          You can stop sharing your location at any point during a drive without
          ending it, and closing the tab stops it too.
        </Section>

        <Section title="How long it's kept">
          Drives and their location trails are deleted 90 days after they
          finish. Planned trips have no location trail and are kept so old links
          keep working.
        </Section>

        <Section title="Who else sees it">
          Your start and destination are sent to OpenRouteService to get a route
          and to OpenChargeMap to find chargers along it, both of which are
          third-party services with their own terms. Nothing else leaves the
          app: no analytics, no advertising, no trackers, and the page loads no
          third-party scripts at all.
        </Section>

        <Section title="Getting something removed">
          Because there are no accounts, we can&apos;t look a person up. If you
          want a specific trip or drive deleted, send the link and we&apos;ll
          delete it.
        </Section>

        <Link
          href="/"
          className="mt-10 inline-block rounded-xl border border-ink-200 px-4 py-2.5 text-sm font-semibold text-ink-700"
        >
          Back to planning
        </Link>
      </div>
    </main>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="mt-7">
      <h2 className="font-display text-lg font-semibold text-ink-900">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-600">{children}</p>
    </section>
  )
}
