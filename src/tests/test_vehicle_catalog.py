"""The curated catalog's invariants, asserted against the literals themselves.

Nothing else in the stack checks this data. `VehicleOut` types `consumption` as
a bare `dict` and `charge_curve` as a bare `list`, so a malformed entry passes
the API untouched. Worse, `db_bootstrap._sync_vehicle_catalog` swallows failures
on purpose — a catalog that will not load must never take the API down — so a
bad entry does not crash anything. It logs "catalog sync failed" once and the
deployed app serves the *previous* catalog indefinitely, which looks exactly
like the deploy having silently not happened.

So this file is where a hand-typed car gets caught. It is pure: no database, no
engine, no fixtures. Every assertion names the offending slug, what breaks
downstream, and what to do about it, because the reader is someone who added a
car three minutes ago and does not yet know this file exists.

`test_db_bootstrap.py` covers the sync mechanism against a real schema. This
covers the data. They should not learn about each other.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from scripts.seed_vehicles import VEHICLES

# `sort_order` is injected from list position when the module imports, so it is
# part of the shape even though no literal in the file writes it.
EXPECTED_KEYS = frozenset(
    {
        "slug",
        "make",
        "model",
        "variant",
        "nameplate_slug",
        "usable_kwh",
        "consumption",
        "charge_curve",
        "max_dc_kw",
        "mass_kg",
        "top_speed_kph",
        "source_note",
        "sort_order",
    }
)

_SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")

# The slowest speed the sweep ever simulates. `cruiseSpeedsFor` in
# frontend/lib/vehicles.ts filters the ladder to `<= top_speed_kph`, so a car
# below this yields an empty list and `Math.max(...[])` is -Infinity.
SLOWEST_CRUISE_KPH = 90.0

_IDS = [car["slug"] for car in VEHICLES]


@pytest.mark.parametrize("car", VEHICLES, ids=_IDS)
class TestEveryCar:
    """One failure header per car, so the slug is visible before the message."""

    def test_key_set_is_exact(self, car: dict) -> None:
        missing = EXPECTED_KEYS - set(car)
        extra = set(car) - EXPECTED_KEYS
        assert not missing and not extra, (
            f"{car.get('slug', '?')}: missing keys {sorted(missing)}, unexpected "
            f"keys {sorted(extra)}. `seed()` calls `Vehicle(**data)`, so an "
            "unexpected key raises TypeError inside the sync that db_bootstrap "
            "swallows — the catalog then silently stops updating."
        )

    def test_slug_is_a_usable_url(self, car: dict) -> None:
        slug = car["slug"]
        assert _SLUG_RE.fullmatch(slug), (
            f"{slug}: slugs are public URLs (/ev/{slug}) and sitemap entries; "
            "keep them lowercase alphanumeric with single hyphens."
        )
        assert len(slug) <= 60, (
            f"{slug}: {len(slug)} chars, but Vehicle.slug is String(60). "
            "aiosqlite stores it happily and MSSQL truncates, so this only "
            "fails in production, inside the swallowed sync."
        )

    def test_text_fits_its_column(self, car: dict) -> None:
        for field in ("make", "model", "variant"):
            value = car[field]
            if value is None:
                continue
            assert len(value) <= 80, (
                f"{car['slug']}: {field} is {len(value)} chars, but the column "
                "is Unicode(80). Same trap as the slug — SQLite accepts it, "
                "MSSQL raises 'String or binary data would be truncated'."
            )

    def test_curve_spans_the_full_soc_range(self, car: dict) -> None:
        curve = car["charge_curve"]
        assert curve, f"{car['slug']}: empty charge curve."
        assert curve[0][0] == 0 and curve[-1][0] == 100, (
            f"{car['slug']}: curve runs {curve[0][0]}%-{curve[-1][0]}%, not "
            "0-100. `curve_power_kw` clamps outside the given range, so a "
            "curve ending at 90% quietly models its last power all the way to "
            "full, and nothing anywhere reports that it did."
        )

    def test_curve_soc_is_strictly_increasing(self, car: dict) -> None:
        socs = [point[0] for point in car["charge_curve"]]
        assert all(len(point) == 2 for point in car["charge_curve"]), (
            f"{car['slug']}: every curve point must be exactly [soc, kw]."
        )
        assert socs == sorted(set(socs)), (
            f"{car['slug']}: SoC values {socs} are not strictly increasing. "
            "The /ev curve table keys its rows on SoC, so a duplicate is a "
            "duplicate React key as well as an ambiguous interpolation."
        )

    def test_curve_power_is_positive(self, car: dict) -> None:
        for soc, kw in car["charge_curve"]:
            assert kw > 0, (
                f"{car['slug']}: {kw} kW at {soc}%. `charge_minutes` clamps to "
                "max(p, 1.0), so a zero becomes 1 kW and yields an absurd stop "
                "time that still renders perfectly."
            )

    def test_max_dc_kw_matches_the_curve_peak(self, car: dict) -> None:
        peak = max(kw for _, kw in car["charge_curve"])
        assert car["max_dc_kw"] == peak, (
            f"{car['slug']}: max_dc_kw is {car['max_dc_kw']} but the curve "
            f"peaks at {peak}. The picker and the assumptions panel print "
            "max_dc_kw; /ev and the JSON-LD compute the peak from the curve. "
            "Disagreeing means one car advertises two different peak powers."
        )

    def test_top_speed_covers_the_slowest_cruise_speed(self, car: dict) -> None:
        assert car["top_speed_kph"] >= SLOWEST_CRUISE_KPH, (
            f"{car['slug']}: top_speed_kph is {car['top_speed_kph']}. Below "
            f"{SLOWEST_CRUISE_KPH:.0f} the speed ladder is empty and "
            "`Math.max(...[])` puts -Infinity in the page's meta description."
        )

    def test_consumption_is_a_usable_quadratic(self, car: dict) -> None:
        consumption = car["consumption"]
        assert consumption.get("model") == "quadratic", (
            f"{car['slug']}: the simulator only implements the quadratic model."
        )
        a = consumption["a_wh_km"]
        b = consumption["b_wh_km_per_kph2"]
        assert a > 0 and b > 0, (
            f"{car['slug']}: a={a}, b={b}. Both must be positive, or the car "
            "gets cheaper to drive the faster it goes and the sweep inverts."
        )

    def test_the_numbers_are_physically_plausible(self, car: dict) -> None:
        """The only check here that catches a *wrong* number, not a broken one.

        Across dozens of hand-typed entries the likeliest real error is a
        decimal slip in `b_wh_km_per_kph2` (0.105 for 0.0105), which is
        structurally perfect and produces a confident, beautifully formatted,
        completely wrong page. These bands are deliberately wide: they are
        meant to catch a factor of ten, not to referee a curation judgement.
        """
        consumption = car["consumption"]
        at_100 = consumption["a_wh_km"] + consumption["b_wh_km_per_kph2"] * 100**2
        assert 120 <= at_100 <= 400, (
            f"{car['slug']}: {at_100:.0f} Wh/km at 100 km/h is outside the "
            "120-400 band every real EV sits in. Check for a decimal slip in "
            "b_wh_km_per_kph2."
        )
        assert 20 <= car["usable_kwh"] <= 250, (
            f"{car['slug']}: {car['usable_kwh']} kWh usable is not a pack size."
        )
        assert 1200 <= car["mass_kg"] <= 4500, (
            f"{car['slug']}: {car['mass_kg']} kg. This is kerb mass plus a "
            "nominal 180 kg of occupants and luggage — check which one was typed."
        )

    def test_source_note_says_something(self, car: dict) -> None:
        note = car["source_note"]
        assert note and len(note) >= 60, (
            f"{car['slug']}: source_note is empty or a stub. It is the only "
            "free text on /ev/<slug>, and the section is hidden entirely when "
            "it is falsy — so a car with no note publishes a page that never "
            "says where its numbers came from."
        )


class TestCatalog:
    """Properties of the list as a whole."""

    def test_slugs_are_unique(self) -> None:
        # Read the list, not `_IDS`: that is a snapshot taken to label the
        # parametrised cases, and asserting against it would only ever compare
        # the catalog to itself.
        duplicates = [s for s, n in Counter(c["slug"] for c in VEHICLES).items() if n > 1]
        assert not duplicates, (
            f"duplicate slugs {duplicates}. `seed()` upserts by slug, so the "
            "second entry overwrites the first: one car vanishes from the "
            "catalog, /ev's count is off by one, and nothing logs a thing."
        )

    def test_names_are_unique(self) -> None:
        names = [(c["make"], c["model"], c["variant"]) for c in VEHICLES]
        duplicates = [n for n, count in Counter(names).items() if count > 1]
        assert not duplicates, (
            f"two cars are printed identically: {duplicates}. The picker shows "
            "make/model/variant and nothing else, so these are two cards a "
            "reader cannot tell apart. Usually a copy-paste whose slug was "
            "edited and whose name was not."
        )

    def test_each_make_is_one_unbroken_run(self) -> None:
        runs = []
        for car in VEHICLES:
            if not runs or runs[-1] != car["make"]:
                runs.append(car["make"])
        split = [make for make, n in Counter(runs).items() if n > 1]
        assert not split, (
            f"{split} appear in more than one run. /ev groups by make in "
            "catalog order and starts a fresh section every time the string "
            "changes, so a split make renders two headings for one brand. A "
            "near-miss spelling ('Skoda' beside 'Škoda') looks the same here."
        )

    def test_no_two_cars_share_a_source_note(self) -> None:
        notes = Counter(c["source_note"] for c in VEHICLES)
        shared = {
            note[:60] + "...": [c["slug"] for c in VEHICLES if c["source_note"] == note]
            for note, n in notes.items()
            if n > 1
        }
        assert not shared, (
            f"cars sharing a source_note: {shared}. Every other word on "
            "/ev/<slug> is template or derived from the numbers, so two cars "
            "with the same note publish pages whose prose is byte-identical — "
            "the near-duplicate cluster search engines pick one winner from. "
            "Keep the shared platform constant and append a sentence about "
            "this car, naming the model year its curve was fitted to."
        )

    def test_a_nameplate_never_spans_two_makes(self) -> None:
        makes: dict[str, set[str]] = {}
        for car in VEHICLES:
            makes.setdefault(car["nameplate_slug"] or car["slug"], set()).add(car["make"])
        mixed = {n: sorted(m) for n, m in makes.items() if len(m) > 1}
        assert not mixed, (
            f"nameplates covering more than one make: {mixed}. The nameplate "
            "page prints one manufacturer in its heading and its JSON-LD, so "
            "a mixed group publishes a page attributing cars to the wrong brand."
        )

    def test_a_nameplate_is_one_unbroken_run(self) -> None:
        runs = []
        for car in VEHICLES:
            key = car["nameplate_slug"] or car["slug"]
            if not runs or runs[-1] != key:
                runs.append(key)
        split = [n for n, count in Counter(runs).items() if count > 1]
        assert not split, (
            f"{split} appear in more than one run. Variants of one car must sit "
            "together, or the picker scatters them and the /ev card for the "
            "nameplate lands in a position that matches none of its variants."
        )

    def test_nameplate_slugs_do_not_collide_with_variant_slugs(self) -> None:
        variants = {c["slug"] for c in VEHICLES}
        nameplates = {c["nameplate_slug"] for c in VEHICLES if c["nameplate_slug"]}
        collisions = sorted(
            n
            for n in nameplates & variants
            if any(c["slug"] == n and c["nameplate_slug"] != n for c in VEHICLES)
        )
        assert not collisions, (
            f"{collisions} name both a nameplate page and a different car's "
            "variant slug. /ev/<slug> resolves nameplates first, so the car "
            "would become unreachable and its legacy URL would stop redirecting."
        )

    def test_file_order_is_the_display_order(self) -> None:
        orders = [c["sort_order"] for c in VEHICLES]
        assert orders == sorted(orders), (
            "sort_order is derived from list position, so this can only fail "
            "if something started writing it by hand again."
        )
