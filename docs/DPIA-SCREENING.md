# DPIA screening

GDPR Article 35. Screened 2026-08-13 by Marc Meijers.

**Conclusion: no full DPIA required. Two mitigations were adopted anyway, and
both are listed at the end.**

This document exists because the question is a fair one. The app records GPS
trails of real journeys and stores the coordinates of what is usually somebody's
home. A reviewer who noticed that and found nothing written down would be right
to ask whether anyone had thought about it. This is the record that someone did.

## Does Art 35(3) apply?

**(a) Systematic and extensive automated evaluation with legal or similarly
significant effects.** No. There is no profiling, no scoring, no decision taken
about a person. The only automated evaluation is a physics simulation of a car.

**(b) Special categories or criminal convictions on a large scale.** No special
category data is collected by design. Location data is not a special category
under Art 9, though it is sensitive in the ordinary sense. The one uncontrolled
input is the free text feedback box, where a sender could volunteer anything;
that is not processing special category data on a large scale, and the mitigation
is a short retention window.

**(c) Systematic monitoring of a publicly accessible area on a large scale.**
This is the limb that engages. Following a drive is systematic monitoring, and a
motorway is a publicly accessible area.

It fails on **large scale**. Applying the WP248 factors:

| Factor | This app |
|---|---|
| Number of data subjects | Small. A personal project with no marketing spend, and location trails only exist for visitors who deliberately start a drive, which is a minority of an already small number. |
| Volume and range of data | Narrow. Position roughly every five minutes and a hand-entered battery percentage. No continuous track, no identity attached, no linkage to any other dataset. |
| Duration | 90 days, enforced in code. A trail covers one journey, not a life. |
| Geographical extent | Wherever a user drives, so not bounded. This is the one factor pointing towards large scale. |

Three of four point away, and the monitoring is not covert or ambient: it happens
only when a person presses start, on their own device, about their own journey,
and they can stop it mid-drive without ending the drive.

## The Dutch and EDPB blacklists

The Autoriteit Persoonsgegevens list of processing requiring a DPIA includes
large scale location data processing. The qualifier is large scale, assessed
above. The EDPB nine criteria give two hits here, sensitive-in-context data and
systematic monitoring, and the guidance treats two criteria as a signal to
consider a DPIA rather than a threshold that mandates one.

## Residual risks and what stands against them

| Risk | Mitigation |
|---|---|
| A share link leaks and exposes a journey between two homes | Tokens are UUID4 and unguessable; trip pages are `noindex`; the privacy page tells people plainly to treat the link like a password, and warns that pasting it into a chat app generates a preview naming both endpoints |
| Location data kept longer than it is useful | 90 days, enforced by `main._purge_loop` rather than by intent |
| Coordinates of a home kept forever in `trips` | Adopted as a mitigation: 24 month retention, `scripts/purge_old_trips.py`. Previously indefinite |
| Analytics tables drifting into a behavioural record | Nightly-rotating visitor pseudonym; 90 day window; route patterns instead of trip ids; buckets instead of raw user agents |
| No way for a person to object to counting | Adopted as a mitigation: an explicit opt-out on `/privacy#counting` that deletes the stored id, alongside GPC and DNT |

## Reassess if

- Usage grows by an order of magnitude, or the app is promoted commercially.
- Continuous or higher frequency location tracking is introduced.
- Location data is combined with any other dataset, or shared with a third party.
- Accounts are added, which would turn every pseudonym into an identified person.
  Opt-in usernames (ROPA activity 6) are deliberately **not** that: there is no
  sign-in, no email and no password, the handle is bound to a secret the browser
  invented, and it attaches only to trips planned after the claim. Reassess if a
  username ever becomes required, gets an email address attached for recovery,
  or is joined to the analytics pseudonyms — any of those makes it an account.
- The public profile list is widened beyond locality-level place names, or a
  directory, search or listing of usernames is added. Today a profile is only
  findable by someone told the name, which is the assumption the screening rests
  on.
- Corridor statistics are sold or handed to a charging operator in any form richer than the current geohash-4 aggregate.
