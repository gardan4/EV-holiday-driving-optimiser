"""Charging networks, and letting a driver rule some of them out.

"Not Ionity" is an ordinary sentence and the plan had no way to hear it. The
reasons are real and none of them is in the data: a subscription that makes one
network half the price of the next, a card that has never once worked at
another, a bad night at a site that is still on the corridor, or simply a
company somebody would rather not give money to. The DP optimises minutes, so
it will keep proposing the same stop for ever, and the only lever the driver had
was to reject that ONE charger and be handed the next one from the same network
twenty kilometres later.

Five rules keep this honest.

**Excluding a network changes the PLAN, never what the app can see.** The route
snapshot keeps every charger on the corridor, because `live.nearest_charger`
and the "I'm plugged in here now" button have to recognise a site whatever its
badge — a driver who takes the only free stall in town has not stopped being at
a charger because they excluded that brand. Only the DP's candidate list is
filtered. Exactly the same split as "arriving anywhere is noticed".

**A slug, never free text.** `NETWORKS` is a closed list and the request is
validated against it, because this is an unauthenticated public endpoint whose
body is stored whole into `Trip.request` — anything else makes it free-form
storage, and a chart of "which networks do people avoid" built on typed strings
would have as many answers as spellings.

**The operator AND the name, which is the OPPOSITE call from `amenities`.**
That module reads the name and refuses the operator, because "Shell Recharge"
is a network that puts posts in car parks and matching it would mark half of
Europe as having a bakery. Here the question IS the network, so the operator
field is the authoritative signal — and the name is needed too, because OCM's
operator is missing or "Unknown" on a large minority of sites while the title
says "Tesla Supercharger Geiselwind" in plain sight. The two failure modes are
not symmetric: a missed match sends somebody to a charger they told us to
avoid, which is the feature not working, while a wrong match costs one stop out
of hundreds on the corridor.

**One place, several badges.** This filter can only remove what the corridor
search handed it, and a motorway services is several rows in OpenChargeMap —
one per operator, a hundred metres apart. Keeping the most powerful of them and
dropping the rest made excluding a network delete whole forecourts rather than
one brand on them; `chargers.location_variants` is what keeps the other badges
alive that far down, and this module is the reason it has to.

**Needles that are words, and no needle that is a word in a language.** `mer`
is a real Norwegian network and also the French for sea, `total` is a common
noun, `bp` is two letters — a filter that quietly drops the chargers on a French
coastal corridor is worse than one that does not know about Mer. So a network
gets in here only when its name is unambiguous, or when a longer form of it is
("aral pulse", never "aral" on its own where a place could carry it). Growing
the list is cheap; growing it wrongly takes chargers away from a route that
needs them, and nothing on the screen would say so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.simulator import ChargerNode


@dataclass(frozen=True)
class Network:
    #: Stable identifier. Stored on trips and counted; never change one.
    slug: str
    #: What a person calls it.
    label: str
    #: Lowercase forms that identify it in an operator title or a site name.
    #: Matched on word boundaries, so "ionity" will not fire inside a longer
    #: word and "aral pulse" only fires as that phrase.
    needles: tuple[str, ...]


# Ordered roughly by how much of Europe's fast-charging they account for, since
# this is also the order the toggles render in. Append-only in practice: a slug
# that disappears breaks the re-plan of every trip that carries it.
NETWORKS: tuple[Network, ...] = (
    Network("tesla", "Tesla Supercharger", ("tesla", "supercharger")),
    Network("ionity", "IONITY", ("ionity",)),
    Network("fastned", "Fastned", ("fastned",)),
    Network("allego", "Allego", ("allego",)),
    Network("shell-recharge", "Shell Recharge", ("shell recharge", "newmotion", "new motion")),
    Network("enbw", "EnBW", ("enbw",)),
    Network("aral-pulse", "Aral pulse", ("aral pulse", "aralpulse")),
    Network("totalenergies", "TotalEnergies", ("totalenergies", "total energies")),
    Network("eon", "E.ON Drive", ("e.on", "eon drive", "eon drive infrastructure")),
    Network("vattenfall", "Vattenfall InCharge", ("vattenfall", "incharge")),
    Network("electra", "Electra", ("electra",)),
    Network("powerdot", "Power Dot", ("powerdot", "power dot")),
    Network("atlante", "Atlante", ("atlante",)),
    Network("ewiva", "Ewiva / Enel X", ("ewiva", "enel x", "enel-x")),
    Network("circle-k", "Circle K", ("circle k", "circlek")),
    Network("bp-pulse", "bp pulse", ("bp pulse", "bppulse")),
    Network("instavolt", "InstaVolt", ("instavolt",)),
    Network("gridserve", "GRIDSERVE", ("gridserve",)),
    Network("osprey", "Osprey", ("osprey",)),
    Network("electrify-america", "Electrify America", ("electrify america",)),
    Network("evgo", "EVgo", ("evgo",)),
    Network("chargepoint", "ChargePoint", ("chargepoint",)),
)

SLUGS: frozenset[str] = frozenset(n.slug for n in NETWORKS)

#: How many a single request may name. Every slug is a real network, so the
#: only reason to send more than a handful is to fill the column up.
MAX_EXCLUDED = len(NETWORKS)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        n.slug,
        re.compile(
            "|".join(rf"(?<!\w){re.escape(x)}(?!\w)" for x in n.needles),
            re.IGNORECASE,
        ),
    )
    for n in NETWORKS
)


def network_of(operator: str | None, name: str | None) -> str | None:
    """Which known network a site belongs to, or None for "we don't know".

    None is not "independent" — most of OCM is operated by somebody this list
    has never heard of, and a caller that renders it as a brand is wrong. It
    exists so a site can be tested against an exclusion, nothing more.
    """
    haystack = f"{operator or ''} {name or ''}"
    if not haystack.strip():
        return None
    for slug, pattern in _PATTERNS:
        if pattern.search(haystack):
            return slug
    return None


def usable(
    nodes: Sequence[ChargerNode], exclude: Iterable[str] | None
) -> list[ChargerNode]:
    """The chargers a plan may stop at, with excluded networks removed.

    Applied to the DP's CANDIDATES and never to the route snapshot — see the
    module note. Returns the list unchanged when nothing is excluded, so the
    overwhelmingly common case costs one truthiness test rather than a regex
    over every site on the corridor.
    """
    wanted = {s for s in (exclude or ()) if s in SLUGS}
    if not wanted:
        return list(nodes)
    return [c for c in nodes if network_of(c.operator, c.name) not in wanted]


def labels(slugs: Iterable[str]) -> list[str]:
    """Human names for a set of slugs, in `NETWORKS` order.

    For messages. A driver who has excluded three networks and cannot get to
    Innsbruck needs to be told which three, or the only actionable thing about
    the failure is missing from it.
    """
    wanted = set(slugs)
    return [n.label for n in NETWORKS if n.slug in wanted]
