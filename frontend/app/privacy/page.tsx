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
          No accounts, no email addresses, nothing tied to a name. Your IP
          address is used to rate-limit abuse and appears in server logs, which
          are kept for 30 days.
        </Section>

        <Section title="Trips are unlisted, not private">
          Planning a trip stores your start, destination, car, departure time
          and the settings you chose, under a long random link. Anyone with that
          link can see the trip, so treat it like a password. Trip pages are
          excluded from search engines.
        </Section>

        <Section title="Following a drive stores where you were">
          If you start a drive, the phone doing the driving sends its position
          every 25 seconds while the tab is open. We keep a coarse trail, a
          point every five minutes or so, plus the battery readings you type in.
          <strong className="block pt-2 font-semibold text-ink-800">
            That trail is a record of a journey you actually made. Anyone with
            the trip link can watch it live and read it afterwards.
          </strong>
          You can stop sharing your location at any point during a drive without
          ending it, and closing the tab stops it too.
        </Section>

        <Section title="How long it's kept">
          Drives and their location trails are deleted 90 days after they
          started. Planned trips are kept indefinitely so old links keep
          working. A planned trip has no location trail, but it does hold the
          start and destination you typed, their coordinates, and the route
          between them.
        </Section>

        <Section title="What we count">
          We count usage ourselves rather than through Google or anyone else: no
          third-party script, no advertising, no profile, and nothing that
          follows you to another site. A visit records the page you opened, as a
          pattern like <em>/trip/…</em>, never which trip, and whether you
          planned a route or started a drive. If you arrived from a link, we
          keep the site and the page it was on, but never the part of the
          address after a <em>?</em>.
          <br />
          <br />
          A visit also records your country, whether you are on a phone, tablet
          or computer, which browser, and how wide the window is to the nearest
          band. That is stored as a handful of words (<em>mobile</em>,{" "}
          <em>Safari</em>, <em>up to 640</em>), never the long identifying string
          your browser actually sends. To tell two visitors apart, your IP
          address and browser are scrambled into a code using a secret that
          changes every night, so the code you get tomorrow has nothing linking
          it to today&apos;s. These counts are deleted after 90 days.
        </Section>

        <Section title="One thing that does remember you">
          Your browser also makes up a random number the first time you visit,
          keeps it, and sends it when you look at a page or plan a trip, so we
          can tell whether anyone comes back. We store a scrambled version of
          it.
          <strong className="block pt-2 font-semibold text-ink-800">
            It is a random number and nothing else. It is not built from your IP
            address, your device or anything about you, so clearing this
            site&apos;s data in your browser really does end it.
          </strong>
          It is never sent when you open a trip someone shared with you, only
          when you plan one yourself. Attached to usage counts it is deleted
          after 90 days; attached to the summary below it is erased after 15
          months.
        </Section>

        <Section title="If your browser asks us not to">
          If your browser sends Global Privacy Control or Do Not Track, none of
          the counting above happens and the random number is never created.
        </Section>

        <Section title="We also summarise where people drive">
          Alongside your trip we keep a rounded-off summary of it, so we can see
          which routes people plan and where the charging network makes a
          journey painful. Your start and destination become a box roughly 20 by
          25 kilometres, the distance is rounded to the nearest 10 km, and the
          rest is the car, the month you were travelling, and how many charging
          stops the best plan needed.
          <strong className="block pt-2 font-semibold text-ink-800">
            No address, street or postcode goes into that summary.
          </strong>
          It does carry the random number described above, so we can tell
          whether someone plans one trip or ten. That is the only place the two
          meet. It is erased after 15 months, and goes immediately if you delete
          the trip. If this summary is ever shared with anyone else, as a
          picture of where electric cars struggle to charge, the random number
          would not be part of it.
        </Section>

        <Section title="Who else sees it">
          Your start and destination are sent to OpenRouteService to get a route
          and to OpenChargeMap to find chargers along it, both third-party
          services with their own terms. The app is hosted on Microsoft Azure.
          Beyond those, nothing: no advertising, no trackers, and the page loads
          no third-party scripts.
          <strong className="block pt-2 font-semibold text-ink-800">
            Pasting a trip link into WhatsApp, Slack or LinkedIn makes that
            platform fetch the page to build a preview, and the preview names
            your start and destination.
          </strong>
          Per-stop navigation links point at Google Maps, and only open if you
          tap one.
        </Section>

        <Section title="Getting something removed">
          Every trip page has a delete button. It removes the trip, any drives
          on it and their location trails, and the rounded-off summary,
          immediately and for good. It works for anyone holding the link, since
          the link is the only key that exists.
        </Section>

        <Section title="If you send feedback">
          The feedback box stores what you wrote, and the email address you type
          in only if you choose to add one. It goes to this app&apos;s own
          database, and nothing else is attached: no name, no location, no
          record of which trips were yours. What you write is also forwarded to
          a private Discord channel so it gets read, including your email
          address if you added one. Leave that field empty if you would rather
          not.
        </Section>

        <Section title="Who runs this">
          A personal project, run by an individual in the Netherlands, not a
          company. The source is public. If you are in the EU you have the right
          to complain to your data protection authority; in the Netherlands that
          is the Autoriteit Persoonsgegevens.
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
