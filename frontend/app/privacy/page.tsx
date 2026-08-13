import type { Metadata } from "next"
import Link from "next/link"
import AppHeader from "@/app/_components/AppHeader"
import CountingToggle from "@/app/_components/CountingToggle"

export const metadata: Metadata = {
  title: "Privacy",
  description: "What this app stores, and for how long.",
}

/**
 * Written because live mode started storing location traces, and a route
 * between two places one of which is usually "home" is the kind of data that
 * deserves a straight answer rather than a policy.
 *
 * It also has to carry what Article 13 requires: who the controller is, how to
 * reach them, what the legal basis is for each thing, and what you can ask for.
 * Those sections are last on the page because nobody arrives wanting them, and
 * plain in tone because a notice written in legal register is one nobody reads.
 *
 * Every claim here has to be true of the code. "Deleted after 90 days" was
 * false for as long as nothing called the purge. Change the page and the
 * behaviour together.
 */
export default function PrivacyPage() {
  return (
    <main className="min-h-screen">
      <AppHeader />
      <div className="mx-auto max-w-2xl px-4 pb-20 pt-8">
        <h1 className="font-display text-3xl font-bold text-ink-900">Privacy</h1>
        <p className="mt-2 text-sm text-ink-500">
          Short version: there are no accounts, we don&apos;t know who you are,
          location data is deleted after 90 days, and you can switch off the
          counting further down this page. If you pick a username, the trips you
          plan after that get a public page — that one is worth reading.
        </p>

        <Section title="There is no sign-in">
          No accounts, no passwords, no email addresses. You can pick a username
          if you want your trips kept in one place, and that is described just
          below — it is still not an account, and nothing about it is tied to a
          name we asked you for. Your IP address is used to rate-limit abuse and
          appears in server logs, which are kept for 30 days.
        </Section>

        <Section title="Trips are unlisted, not private">
          Planning a trip stores your start, destination, car, departure time
          and the settings you chose, under a long random link. Anyone with that
          link can see the trip, so treat it like a password. Trip pages are
          excluded from search engines.
        </Section>

        <Section title="A username, if you pick one" id="username">
          By default a trip is only reachable through its link, and nothing
          connects the trips you plan to each other. Picking a username in the
          &ldquo;Your trips&rdquo; panel changes that on purpose: it gives you a
          page at <em>/u/yourname</em> listing what you have planned.
          <strong className="block pt-2 font-semibold text-ink-800">
            That page is public. Anyone who knows or guesses the username can
            read it, without an account and without being asked.
          </strong>
          It shows the town you set off from, the town you were going to, the
          distance, the car, the date and how many charging stops the best plan
          needed. It does not show your exact start or destination — those are
          cut back to the nearest place name before they ever leave the server —
          and it does not show the route. The trip page behind each entry does,
          and that still needs the link.
          <br />
          <br />
          Choosing the name is the moment you opt in, so it only applies going
          forward: trips you planned before are not added to it, ever. Your
          browser makes up a second random number to prove the name is yours. It
          is not sent when you read anything, only when you plan a trip or
          change the username itself.
          <strong className="block pt-2 font-semibold text-ink-800">
            Releasing the username removes the page immediately and detaches
            every trip from it permanently.
          </strong>
          Your trips and their links survive that — releasing a name is not the
          same as deleting your journeys, and the delete button on each trip is
          still how you do that. A username nobody has released and nothing is
          published under is deleted automatically after two years. If you lose
          the browser that holds the number, email us and we will release the
          name for you.
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
          started. Planned trips and their summaries are deleted after two
          years, so a link you shared last summer still works this summer but
          does not sit on a server forever. A planned trip has no location
          trail, but it does hold the start and destination you typed, their
          coordinates, and the route between them. Feedback is deleted after two
          years. Usage counts go at 90 days. Server logs go at 30 days. A
          username lasts until you release it, or is removed automatically two
          years after you claimed it if nothing is published under it any more.
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
          Two more things are counted, both of them bare counts: that a trip was
          deleted, and that a username was released. Neither records which trip
          or which name — by the time we count it, the trip or the username is
          already gone. They exist because everything else we count is a count
          of things that still exist, so without them a summary that shrank
          would look like people had stopped using the app.
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

        <Section title="Something that does remember you" id="counting">
          For the counting, your browser also makes up a random number the first
          time you visit, keeps it, and sends it when you look at a page or plan
          a trip, so we can tell whether anyone comes back. We store a scrambled
          version of it.
          <strong className="block pt-2 font-semibold text-ink-800">
            It is a random number and nothing else. It is not built from your IP
            address, your device or anything about you, so clearing this
            site&apos;s data in your browser really does end it.
          </strong>
          It is never sent when you open a trip someone shared with you, only
          when you plan one yourself. Attached to usage counts it is deleted
          after 90 days; attached to the summary below it is erased after 15
          months.
          <br />
          <br />
          You can turn all of the counting off here. It takes effect straight
          away, on this browser, and it sticks until you change it back.
          <CountingToggle />
          <span className="block pt-3">
            If you picked a username, that uses a second random number of its
            own, described further up. This switch does not touch it, because it
            is a key to something you asked for rather than a way of counting
            you — releasing the username is how you end that one.
          </span>
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
          Cloudflare sits in front of this app and handles every request before
          it reaches us, so it sees your IP address and which page you asked
          for, including the random part of a trip link. Its network is
          worldwide, so a request can pass through a server outside the EU. The
          app itself and its database run on Microsoft Azure.
          <br />
          <br />
          Your start and destination are sent to OpenRouteService to get a route
          and to OpenChargeMap to find chargers along it, both third-party
          services with their own terms. Feedback, and the email address you
          type in if you add one, is forwarded to a private Discord channel.
          Discord is a company in the United States, so that one leaves the EU.
          Microsoft, Cloudflare and Discord all offer the standard contractual
          clauses the GDPR asks for when data goes to another country, and we
          rely on those.
          <strong className="block pt-2 font-semibold text-ink-800">
            Pasting a trip link into WhatsApp, Slack or LinkedIn makes that
            platform fetch the page to build a preview, and the preview names
            your start and destination.
          </strong>
          Beyond those, nothing: no advertising, no trackers, and the page loads
          no third-party scripts. Per-stop navigation links point at Google
          Maps, and only open if you tap one.
        </Section>

        <Section title="Why we're allowed to keep any of it">
          The law wants a reason for each thing, so here they are.
          <br />
          <br />
          Planning a trip and keeping it alive at its link is us doing the thing
          you asked for, which is Article 6(1)(b). So is the username and the
          public page it creates: you asked for it by claiming the name, and
          releasing it is how you take it back. Following a drive runs on
          your consent, given when you press start and withdrawn when you stop
          sharing or delete the trip, which is Article 6(1)(a). Counting usage,
          the rounded-off summary and the random number run on our own
          legitimate interest in knowing whether this app works and whether
          anyone uses it, which is Article 6(1)(f), and the switch above is how
          you say no. Rate limits and server logs run on the same legitimate
          interest in keeping the app standing up.
        </Section>

        <Section title="What you can ask for">
          You can ask for a copy of what we hold about you, ask us to correct
          it, ask us to delete it, ask us to stop using it or hold it still, ask
          for it in a form you can take elsewhere, and object to anything we do
          on legitimate interest. Email the address at the bottom of this page.
          <br />
          <br />
          For a trip, the link is the only identifier there is, so send it to us
          or press the delete button, which does the same job immediately.
          <strong className="block pt-2 font-semibold text-ink-800">
            For the counting, there is genuinely nothing to look up.
          </strong>
          We hold a scrambled code and no name, no email and no account, and we
          cannot work backwards from it to you. Sending us the number out of
          your own browser storage would not help either, because what we keep
          is a hash of it made with a key. That is not us being difficult. It is
          the reason those tables were built this way, and it means the law does
          not require us to go looking. If you can show us how to find your
          rows, we will.
        </Section>

        <Section title="Getting something removed">
          Every trip page has a delete button. It removes the trip, any drives
          on it and their location trails, and the rounded-off summary,
          immediately and for good. It works for anyone holding the link, since
          the link is the only key that exists. A deleted trip also disappears
          from your public page, if you have one.
          <br />
          <br />
          If you picked a username, the &ldquo;Your trips&rdquo; panel has a
          release button. It takes the public page down at once and unlinks
          every trip from the name for good.
        </Section>

        <Section title="If you send feedback">
          The feedback box stores what you wrote, and the email address you type
          in only if you choose to add one. It goes to this app&apos;s own
          database, and nothing else is attached: no name, no location, no
          record of which trips were yours. What you write is also forwarded to
          a private Discord channel so it gets read, including your email
          address if you added one. Leave that field empty if you would rather
          not. It is deleted after two years.
        </Section>

        <Section title="Who runs this">
          A personal project, run by an individual in the Netherlands, not a
          company. The source is public. For anything on this page, including
          the requests above, the person responsible for the data is:
          <span className="mt-2 block font-semibold text-ink-800">
            Marc Meijers
            <br />
            <a
              className="underline underline-offset-2"
              href="mailto:Marcmeijers@foundworks.ai"
            >
              Marcmeijers@foundworks.ai
            </a>
          </span>
          <span className="block pt-2">
            If you are in the EU you also have the right to complain to your
            data protection authority. In the Netherlands that is the Autoriteit
            Persoonsgegevens.
          </span>
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
  id,
  children,
}: {
  title: string
  id?: string
  children: React.ReactNode
}) {
  return (
    // `scroll-mt` so the footer's "Counting: on" link lands with the heading
    // clear of the sticky header rather than tucked underneath it.
    <section id={id} className="mt-7 scroll-mt-24">
      <h2 className="font-display text-lg font-semibold text-ink-900">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-600">{children}</p>
    </section>
  )
}
