# Personal data breach procedure

GDPR Articles 33 and 34. Last reviewed 2026-08-13.

Written before it is needed, because 72 hours is not enough time to also work
out what the process is. There is one person here, so this is deliberately short
enough to actually follow at 2am.

**The clock starts when you become aware a breach may have happened, not when
you have finished confirming it.** Awareness of a reasonable likelihood is
enough to start counting.

## What counts

Any accidental or unlawful destruction, loss, alteration, unauthorised
disclosure of, or access to personal data. For this app the realistic cases are:

- Azure SQL exposed or dumped, which would mean trip coordinates and location trails.
- `SECRET_KEY` leaking. This is the salt behind both pseudonyms, so a leak plus a copy of `app_events` makes the daily visitor hash reversible by brute force over the IP space.
- The Discord webhook URL leaking, which would expose feedback and any email addresses in that channel.
- `STATS_TOKEN` leaking, which would give someone the admin console's read access to every aggregate.
- A bug that serves one visitor's trip to another, or that puts a trip id into the analytics table.
- Loss of the database with no working backup, which is a breach of availability and still notifiable.

A failed purge is not a breach. It is a compliance failure against the privacy
page, which needs fixing but not reporting.

## Immediate steps

1. **Stop the bleeding.** Rotate the credential, take the route offline, or scale the app down. Availability loss is preferable to continuing disclosure.
2. **Write down the time you became aware** and what made you aware. This timestamp is the one the 72 hours runs from and it will be asked for.
3. **Preserve evidence.** Snapshot Log Analytics before the 30 day window rolls it off. Do not clean up the environment before capturing its state.

## Assess

Record answers to these, because they are what the notification form asks:

- What happened, and when did it start and end?
- Which tables and which categories of data? Coordinates, location trails, pseudonyms, feedback and email addresses are the ones that matter here.
- Roughly how many data subjects and how many records?
- Is the data pseudonymised, and is the salt also compromised? A hash whose salt is intact is much weaker evidence of risk than one whose salt leaked with it.
- What are the likely consequences?
- What has been done, and what will be done, to fix it and stop a repeat?

## Notify

**The Autoriteit Persoonsgegevens, within 72 hours**, unless the breach is
unlikely to result in a risk to the rights and freedoms of natural persons.
Report at autoriteitpersoonsgegevens.nl. If the full picture is not available
yet, file anyway and supply the rest in phases. A late notification needs a
reasoned explanation for the delay, which is worse than an incomplete one.

Assume a risk, and therefore notify, if location trails or trip coordinates were
exposed. A route between a home and a holiday destination identifies where
somebody lives and when they are away from it.

**The people affected, without undue delay**, if the risk to them is high.
There are no accounts and no email addresses for visitors, so individual
notification is usually impossible. Art 34(3)(c) allows a public communication
instead: a prominent notice on the site, on the landing page and on `/privacy`,
saying what happened, what it means for them, and what to do about it. For
feedback senders who supplied an address, email them directly.

**No notification is needed** where the data was rendered unintelligible, for
example a dump of `app_events` alone with `SECRET_KEY` intact. Record that
reasoning rather than relying on remembering it.

## Record it either way

Art 33(5) requires every breach to be documented, including the ones judged not
notifiable, with the facts, the effects and the remedial action. Keep them in
this directory as `breach-YYYY-MM-DD.md`. The regulator can ask to see the log,
and an empty log with no policy behind it is itself a finding.
