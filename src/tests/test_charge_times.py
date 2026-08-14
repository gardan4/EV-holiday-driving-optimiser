"""What a catalog car actually costs you at a charger.

The curves shipped for a year fitted by eye, drifting up to 26% from measured
behaviour in BOTH directions, and nothing noticed — because every test asserted
properties of the SHAPE (monotone SoC, positive kW, peak equals `max_dc_kw`)
and none asserted the number a curve exists to produce. A curve can satisfy
every structural rule in `test_vehicle_catalog.py` and still claim a Model Y
charges in 27 minutes when it takes 33.

So these tests assert TIME. Three kinds, and they fail for different reasons:

* `TestMeasuredCurveTimes` pins the cars rebuilt from measured data to the
  minute. It is a regression guard, not a physical claim — it fails when
  somebody edits a curve, and the question to ask is "did I mean to?".
* `TestEveryCarIsPlausible` is a physical claim about all 70, including the
  hand-drawn ones nobody has a source for. It is deliberately loose: it exists
  to catch a curve that is nonsense, not one that is merely uncertain.
* `TestAttributionSurvives` is a LICENCE obligation. The measured curves come
  from a CC BY 4.0 dataset, and attribution-only is still a condition — a
  `source_note` reworded without its citation is a licence breach, not a typo.
"""

from __future__ import annotations

import pytest

from app.services.simulator import VehicleParams, charge_minutes
from scripts.seed_vehicles import VEHICLES

CITATION_MARK = "doi:10.1184/R1/30570653"

# 10-80% minutes at a charger fast enough not to be the limit, as the catalog
# currently models them. Generated from the committed curves, then checked
# against the published figure where one exists: the Model Y's 32.6 matches an
# independently measured 32.6, which is the number that started all of this.
EXPECTED_10_80 = {
    "tesla-modely-lr": 32.6,
    "tesla-models-lr": 30.5,
    "tesla-models-plaid": 31.9,
    "tesla-modelx-lr": 31.1,
    "tesla-modelx-plaid": 31.8,
    "hyundai-ioniq5-73": 19.0,
    "hyundai-ioniq5-84": 18.8,
    "hyundai-ioniq6-74-rwd": 18.9,
    "hyundai-kona-64": 48.2,
    "kia-ev6-74-rwd": 16.6,
    "kia-ev3-78": 32.4,
    "bmw-i4-edrive40": 34.0,
    "bmw-i5-edrive40": 30.4,
    "bmw-ix1-edrive20": 34.9,
    "ford-mustang-mach-e-er": 48.5,
    "audi-q4-45": 34.4,
    "renault-megane-60": 34.7,
}

_BY_SLUG = {car["slug"]: car for car in VEHICLES}


def params(car: dict) -> VehicleParams:
    return VehicleParams(
        usable_kwh=car["usable_kwh"],
        a_wh_km=car["consumption"]["a_wh_km"],
        b_wh_km_per_kph2=car["consumption"]["b_wh_km_per_kph2"],
        charge_curve=tuple((float(s), float(k)) for s, k in car["charge_curve"]),
        charge_efficiency=float(car.get("charge_efficiency", 0.92)),
    )


def minutes_10_80(car: dict) -> float:
    """At a site that is never the constraint, so this is the car's own ceiling."""
    return charge_minutes(params(car), 10.0, 80.0, car["max_dc_kw"] * 10)


class TestMeasuredCurveTimes:
    @pytest.mark.parametrize("slug", sorted(EXPECTED_10_80))
    def test_10_80_has_not_drifted(self, slug: str) -> None:
        car = _BY_SLUG.get(slug)
        assert car is not None, (
            f"{slug} is pinned here but no longer in the catalog. If it was "
            "renamed, move the entry; slugs are permanent, so a disappearing "
            "one means a share link somewhere now 404s."
        )
        got = minutes_10_80(car)
        want = EXPECTED_10_80[slug]
        assert got == pytest.approx(want, rel=0.03), (
            f"{slug}: 10-80% is now {got:.1f} min, was {want:.1f}. These curves "
            "were rebuilt from measured data and match published tests, so a "
            "change here is either a deliberate re-fit (update this number and "
            "say why in the source_note) or an accident."
        )

    def test_every_measured_car_is_pinned(self) -> None:
        rebuilt = {c["slug"] for c in VEHICLES if CITATION_MARK in (c["source_note"] or "")}
        assert rebuilt == set(EXPECTED_10_80), (
            f"measured cars not pinned: {sorted(rebuilt - set(EXPECTED_10_80))}; "
            f"pinned but no longer measured: {sorted(set(EXPECTED_10_80) - rebuilt)}. "
            "Rebuilding a car's curve without pinning its time leaves exactly "
            "the gap this file exists to close."
        )


class TestEveryCarIsPlausible:
    """Loose physical bounds over the WHOLE catalog, hand-drawn curves included."""

    @pytest.mark.parametrize("car", VEHICLES, ids=[c["slug"] for c in VEHICLES])
    def test_10_80_is_not_absurd(self, car: dict) -> None:
        got = minutes_10_80(car)
        assert 8.0 <= got <= 90.0, (
            f"{car['slug']}: 10-80% takes {got:.1f} min. Nothing on sale is "
            "outside 8-90 minutes; this is a broken curve, not a slow car."
        )

    @pytest.mark.parametrize("car", VEHICLES, ids=[c["slug"] for c in VEHICLES])
    def test_average_power_is_a_sane_fraction_of_peak(self, car: dict) -> None:
        kwh = car["usable_kwh"] * 0.70
        avg = kwh / (minutes_10_80(car) / 60.0)
        share = avg / car["max_dc_kw"]
        assert 0.20 <= share <= 0.95, (
            f"{car['slug']}: averages {avg:.0f} kW over 10-80%, which is "
            f"{share:.0%} of its {car['max_dc_kw']:.0f} kW peak. Below 20% the "
            "curve tapers so hard the peak is a fiction; above 95% it barely "
            "tapers at all, which no chemistry does."
        )


class TestAttributionSurvives:
    """CC BY 4.0 is attribution-ONLY, which makes attribution the whole licence."""

    def test_rebuilt_curves_keep_their_citation(self) -> None:
        expected = set(EXPECTED_10_80)
        cited = {c["slug"] for c in VEHICLES if CITATION_MARK in (c["source_note"] or "")}
        missing = expected - cited
        assert not missing, (
            f"curves rebuilt from the CC BY dataset with no citation left in "
            f"their source_note: {sorted(missing)}. The note is where we credit "
            "it, and /ev/<slug> is where that credit is published — dropping it "
            "while keeping the data is a licence breach, not an edit."
        )
