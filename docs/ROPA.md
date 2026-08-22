# Record of processing activities

GDPR Article 30. Last reviewed 2026-08-13.

The Art 30(5) exemption for organisations under 250 people does not apply here.
It is lifted when processing is more than occasional, and this app processes on
every visit, so the record has to exist regardless of how small the project is.

## Controller

| | |
|---|---|
| Controller | Marc Meijers, a private individual in the Netherlands |
| Contact | Marcmeijers@foundworks.ai |
| Data protection officer | None. Art 37 does not require one: this is not a public authority, and neither large scale systematic monitoring nor large scale special category data applies. See `DPIA-SCREENING.md`. |
| Representative (Art 27) | Not applicable, the controller is established in the EU |
| Supervisory authority | Autoriteit Persoonsgegevens |

## Processing activities

### 1. Planning and serving a trip

| | |
|---|---|
| Purpose | Compute a cruise speed and charging plan, and keep it available at its share link |
| Legal basis | Art 6(1)(b), necessary to provide the service the visitor asked for |
| Data subjects | Anyone who plans a trip |
| Categories | Exact origin and destination coordinates and the place names typed, departure time, vehicle choice, state of charge settings, driving and charging preferences (including which charging networks to avoid), the computed route and plan |
| Table | `trips` (`request` and `result` JSON) |
| Retention | 24 months from creation, enforced by `scripts/purge_old_trips.py` on `main._purge_loop` |
| Recipients | OpenRouteService (routing and reverse geocoding), OpenChargeMap (charger search), Microsoft Azure (hosting), Cloudflare (edge) |

Note: origin and destination are usually a home address. This is the most
sensitive thing the app holds outside the live location trail.

Note: the planner's "Here" button sends the device's own GPS position to
OpenRouteService to be turned into a place name (`GET /api/geocode/reverse`).
It is Art 6(1)(a) consent, given by pressing it and by the browser's own
location permission, and it is the only path by which a device position — as
opposed to a place somebody typed — becomes a trip origin. Nothing about the
position is stored beyond the resulting origin, which is stored exactly as a
typed one is.

### 2. Following a live drive

| | |
|---|---|
| Purpose | Show a driver, and anyone holding the trip link, where the car is against its plan |
| Legal basis | Art 6(1)(a) consent, given by starting a drive, withdrawn by stopping sharing, closing the tab or deleting the trip |
| Data subjects | Anyone who starts a drive |
| Categories | GPS position roughly every five minutes, state of charge readings entered by hand, timestamps, replans and reroutes |
| Tables | `trip_runs`, `trip_events` |
| Retention | 90 days from the drive starting, enforced by `scripts/purge_old_runs.py` |
| Recipients | Microsoft Azure, Cloudflare, and OpenRouteService/OpenChargeMap on a reroute only |

Note: a drive that leaves its planned route for more than a couple of minutes
asks for a road from where the car actually is (`POST /runs/{id}/reroute`),
which sends that one position to OpenRouteService and the corridor around it to
OpenChargeMap. It is the only point at which a live position leaves us. It is
covered by the same consent as the drive itself and stops when sharing does.

### 3. Usage counting

| | |
|---|---|
| Purpose | Know whether the app works and whether anyone uses it |
| Legal basis | Art 6(1)(f) legitimate interest. See `LIA-CLIENT-ID.md` for the balancing test on the persistent identifier |
| Data subjects | Every visitor who has not opted out |
| Categories | Daily rotating pseudonym of IP plus user agent; hashed persistent browser id; route pattern (never a trip id); referrer host and path with the query string dropped; country from `CF-IPCountry`; bucketed device, browser, OS and viewport band; campaign slug |
| Table | `app_events` |
| Retention | 90 days, enforced by `scripts/purge_old_events.py` |
| Recipients | Microsoft Azure, Cloudflare |
| Objection | GPC, Do Not Track, or the toggle on `/privacy#counting`, which also deletes the stored id |

The daily pseudonym is salted with `SECRET_KEY` and the current UTC date, so it
cannot be reversed and cannot be followed across a midnight boundary. Raw user
agents, raw viewport widths and full referrer URLs are never written.

### 4. Corridor statistics

| | |
|---|---|
| Purpose | See which routes people plan and where the charging network makes a journey painful |
| Legal basis | Art 6(1)(f) legitimate interest |
| Data subjects | Anyone who plans a trip |
| Categories | Geohash-4 origin and destination (roughly 20 by 25 km), distance to the nearest 10 km, vehicle, departure month, transit countries, stop count, feasibility, hashed persistent browser id |
| Table | `trip_stats` |
| Retention | The `client_id` column is nulled at 15 months (`scripts/purge_old_trip_stat_ids.py`); the row is deleted with its trip at 24 months, or immediately when the trip is deleted |
| Recipients | Microsoft Azure, Cloudflare |

Derived on write from data already held in `trips`. Nothing new is collected.
Coarse by construction, so any export cannot carry a doorstep.

### 5. Feedback

| | |
|---|---|
| Purpose | Receive and reply to bug reports and comments |
| Legal basis | Art 6(1)(f) legitimate interest in running the app; the optional email address is provided voluntarily by the sender for the sole purpose of a reply |
| Categories | Free text message, optional email address, the page it was sent from |
| Table | `feedback` |
| Retention | 24 months, enforced by `scripts/purge_old_feedback.py` |
| Recipients | Microsoft Azure, Cloudflare, Discord |

Free text is uncontrolled input. A sender can put anything in it, including
special category data, which is a reason to keep the retention window short
rather than a reason the processing is unlawful.

### 6. Public trip profiles (usernames)

| | |
|---|---|
| Purpose | Let a visitor keep the trips they plan in one place, and share that list under a name of their choosing |
| Legal basis | Art 6(1)(b), necessary to provide a feature the visitor asked for by claiming the name |
| Data subjects | Anyone who claims a username |
| Categories | The username itself — free text chosen by the visitor, so it may be a real name; a hashed random secret generated by their browser; the stamp linking that secret to each trip they plan afterwards |
| Tables | `profiles`, `trips.owner_hash` |
| Retention | Until released by the holder, which is immediate and also detaches every trip; otherwise deleted 24 months after being claimed once no trip carries the stamp (`scripts/purge_old_profiles.py`) |
| Recipients | Microsoft Azure, Cloudflare |

**This is the one processing activity here that publishes.** The list at
`/u/<username>` is readable by anyone who knows or guesses the name, without
authentication. That is the purpose rather than a weakness, and the mitigations
are what keep it proportionate: it is strictly opt-in and the claim is the
consent moment, so trips planned beforehand are never added; place names are
reduced to their locality server-side (`api/users.locality`), so the published
list cannot carry a street address even though `trips` still holds the exact
coordinates behind each share link; and release is immediate, complete and
irreversible.

The username is uncontrolled input and may identify its subject, which is a
reason the release control has to stay one click away rather than a reason the
processing is unlawful. The secret is never stored raw, so a copy of `profiles`
is a list of names and unusable hashes rather than a set of working keys.

### 7. Abuse prevention and operations

| | |
|---|---|
| Purpose | Rate limiting, and diagnosing faults |
| Legal basis | Art 6(1)(f) legitimate interest in keeping a free service available |
| Categories | IP address in memory for rate limit buckets; IP address and request metadata in server logs |
| Storage | slowapi in-process store; Azure Log Analytics |
| Retention | Log Analytics `retentionInDays: 30` (`infra/main.bicep`) |

## Processors and recipients

| Recipient | Role | Location | Transfer basis |
|---|---|---|---|
| Microsoft Azure | Hosting and database | Region of the resource group | Within the EU where the resource group is in the EU; otherwise Microsoft's standard contractual clauses |
| Cloudflare | Edge, TLS termination, DDoS | Global anycast network | Standard contractual clauses |
| OpenRouteService (HeiGIT, Heidelberg) | Geocoding and routing | Germany | Within the EU |
| OpenChargeMap | Charger data | United Kingdom | UK adequacy decision |
| Discord | Feedback notifications, including any email address supplied | United States | Standard contractual clauses / EU-US Data Privacy Framework |
| GitHub (GHCR) | Container registry | United States | No personal data in images |

Azure region is inherited from the resource group (`infra/main.bicep` uses
`resourceGroup().location`). **Verify the deployed region is in the EU, and
update the transfer basis above if it is not.**

## Technical and organisational measures (Art 32)

- No authentication surface and no user accounts, so no credential store to breach.
- Trip share tokens are UUID4, unguessable, and `noindex` via `frontend/proxy.ts`.
- Pseudonyms are SHA-256 hashes salted with `SECRET_KEY`; the visitor hash also takes the UTC date, so it rotates nightly.
- The persistent browser id is stored only as a hash, and is validated against a strict v4 pattern before it is accepted.
- User agents, viewport widths and referrer query strings are reduced or dropped before storage, never stored raw.
- Route patterns replace trip ids, so a share token never lands in the analytics table.
- Content Security Policy allows no external origins. No third-party scripts.
- HTTPS everywhere, security headers set in `frontend/proxy.ts`.
- Azure SQL is firewalled to the App Service outbound IPs.
- Rate limiting keyed on a client IP that cannot be chosen by the caller (`app/core/rate_limit.py`).
- The admin console runs locally only, behind an encrypted httpOnly cookie exchanged for `STATS_TOKEN`, and is default-deny when that token is empty.
- Retention is enforced in code on a daily loop, not by policy.

## Review

Review when a new table holding personal data is added, when a processor
changes, or annually, whichever comes first.
