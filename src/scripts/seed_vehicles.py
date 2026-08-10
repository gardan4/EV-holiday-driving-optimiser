"""Idempotent vehicle-catalog seeder (upsert by slug).

Run manually with `uv run python -m scripts.seed_vehicles`. `db_bootstrap`
also runs it on every boot, not just on an empty table, so the catalog in the
running database is always whatever this file says.

Data provenance: consumption is a fitted quadratic Wh/km(v) = a + b·v² and the
DC charge curve is piecewise-linear (soc% → kW, cable-side), both hand-curated
to match public fast-charge tests and range tests (Fastned charging guides, P3
Charging Index, InsideEVs/Bjørn Nyland 1000 km tests). They are deliberately
conservative approximations — good enough to compare cruise speeds, not lab data.

`mass_kg` is kerb mass plus a nominal 180 kg of occupants and luggage; it only
feeds the elevation term. `top_speed_kph` is the electronically limited top
speed — the sweep stops there, because simulating a Born at 175 is fiction.

Conventions this file keeps, all of them enforced by
`tests/test_vehicle_catalog.py` — worth reading before adding a car, because
`db_bootstrap` deliberately swallows sync failures, so a malformed entry does
not crash the API. It logs and leaves the deployed catalog on yesterday's data.

* **Order is meaning.** `sort_order` is derived from position in `VEHICLES`
  (see the loop below the list), so put a car where you want it to appear.
  Each make must be one unbroken run: `/ev` starts a new section every time
  the make string changes, so a stray `Skoda` next to `Škoda`, or one Tesla
  parked away from the others, silently renders two sections for one brand.
* **Never change a slug.** The upsert matches on it and never deletes, so a
  rename creates a second row and strands the first in the database and the
  API forever. It is also the public `/ev/<slug>` URL. To rename a car for
  readers, change `make`/`model`/`variant`, which are overwritten in place.
* **Every `source_note` is unique.** It is the only free text on a car's `/ev`
  page — everything else there is template or derived — so a bare shared
  constant produces pages whose prose is byte-identical. Use a shared constant
  for the platform (three or more cars sharing a curve family earns one), then
  append at least one sentence about *this* car, naming the model year the
  curve was fitted to.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_sync_engine
from app.models import Vehicle

logger = logging.getLogger("seed_vehicles")

_MEB_NOTE = (
    "VW MEB platform curve: holds peak to ~30% SoC, then near-linear taper. "
    "Curated from Fastned model guide + P3 Charging Index style tests."
)

_EGMP_NOTE = (
    "Hyundai/Kia E-GMP 800 V platform — peak power held nearly flat to ~45-50% "
    "SoC, then a steep taper, so these cars want short stops taken low. "
    "Curated from Fastned model guides + P3 Charging Index style tests."
)

# The Chinese-brand entries are newer and less cross-checked than the European
# ones: there are fewer independent fast-charge tests per model, and several are
# still receiving OTA charging changes. Say so in the car's own note rather than
# only in a comment — the assumptions panel shows `source_note` to the reader,
# and a number whose confidence isn't stated reads as a measurement.
_CHINA_CAVEAT = (
    "Approximated from published fast-charge and motorway-consumption tests. "
    "Fewer independent runs exist for this model than for the European cars "
    "here, so treat the curve — especially above 70% SoC — as indicative."
)

_US_NOTE = (
    "Approximated from published EPA figures and fast-charge tests. American "
    "cars are rated on the EPA cycle rather than WLTP, and the consumption fit "
    "here is a motorway fit, so it will not reproduce an EPA range figure — "
    "that is the point, since EPA combined understates a 120 km/h cruise badly."
)

_TRUCK_NOTE = (
    "A 2.7-tonne brick: the quadratic term is roughly double a saloon's, so "
    "speed costs it far more than it costs anything European in this list. "
    + _US_NOTE
)

_BYD_BLADE_NOTE = (
    "BYD Blade LFP pack: a modest peak held broadly flat rather than a tall "
    "spike, and a long slow tail, so these cars want to be unplugged early. "
    + _CHINA_CAVEAT
)

_VOLVO_CMA_NOTE = (
    "Volvo CMA 82 kWh pack (2024 rear-motor Extended Range): ~200 kW peak that "
    "tapers from ~30% SoC. All Volvos are limited to 180 km/h. Curated from "
    "Fastned model guide + P3 Charging Index style tests."
)

VEHICLES: list[dict] = [
    {
        "slug": "vw-id3-pro-58",
        "make": "Volkswagen",
        "model": "ID.3",
        "variant": "Pro 58 kWh",
        "usable_kwh": 58.0,
        "consumption": {"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0106},
        "charge_curve": [
            [0, 50], [5, 118], [10, 120], [30, 120], [35, 105], [45, 85],
            [55, 68], [65, 55], [75, 42], [85, 30], [95, 18], [100, 10],
        ],
        "max_dc_kw": 120.0,
        "mass_kg": 1940.0,
        "top_speed_kph": 160.0,
        "source_note": _MEB_NOTE
        + " Fitted to the 2023 Pro. The lightest body on this pack, so it sets"
        + " the floor the heavier 120 kW MEB cars are measured against.",
    },
    {
        "slug": "vw-id4-pro-77",
        "make": "Volkswagen",
        "model": "ID.4",
        "variant": "Pro 77 kWh",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0125},
        "charge_curve": [
            [0, 55], [5, 130], [10, 135], [30, 135], [40, 110], [50, 90],
            [60, 75], [70, 60], [80, 45], [90, 28], [100, 12],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2300.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_NOTE + " SUV body → higher consumption than ID.3.",
    },
    {
        "slug": "vw-id7-pro-77",
        "make": "Volkswagen",
        "model": "ID.7",
        "variant": "Pro 77 kWh",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0105},
        "charge_curve": [
            [0, 70], [5, 165], [10, 175], [35, 172], [45, 140], [55, 112],
            [65, 88], [75, 65], [85, 42], [95, 22], [100, 10],
        ],
        "max_dc_kw": 175.0,
        "mass_kg": 2350.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_NOTE
        + " Cd 0.23 saloon — a big car that is genuinely efficient at motorway speed.",
    },
    {
        "slug": "cupra-born-58",
        "make": "Cupra",
        "model": "Born",
        "variant": "58 kWh",
        "usable_kwh": 58.0,
        "consumption": {"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0105},
        "charge_curve": [
            [0, 50], [5, 118], [10, 120], [30, 120], [35, 105], [45, 85],
            [55, 68], [65, 55], [75, 42], [85, 30], [95, 18], [100, 10],
        ],
        "max_dc_kw": 120.0,
        "mass_kg": 1900.0,
        "top_speed_kph": 160.0,
        "source_note": _MEB_NOTE
        + " Fitted to the 2022-2024 58 kWh Born, which shares the ID.3's pack"
        + " and curve; the wider standard tyres cost it a little consumption.",
    },
    {
        "slug": "cupra-born-77",
        "make": "Cupra",
        "model": "Born",
        "variant": "77 kWh",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 58.0, "b_wh_km_per_kph2": 0.0110},
        "charge_curve": [
            [0, 55], [5, 130], [10, 135], [30, 135], [40, 110], [50, 90],
            [60, 75], [70, 60], [80, 45], [90, 28], [100, 12],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2080.0,
        "top_speed_kph": 160.0,
        "source_note": _MEB_NOTE
        + " Fitted to the 2023 77 kWh Born. The bigger pack lifts the peak to"
        + " 135 kW and holds it about five points of SoC longer than the 58.",
    },
    {
        "slug": "skoda-enyaq-77",
        "make": "Škoda",
        "model": "Enyaq",
        # Year-qualified because the 2025 Enyaq 85 sits next to it in the
        # picker, and "Enyaq 80" beside "Enyaq 85" reads as two trims of one
        # generation rather than two generations with different charge curves.
        "variant": "80 (77 kWh, 2021-2024)",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0122},
        "charge_curve": [
            [0, 55], [5, 130], [10, 135], [30, 135], [40, 110], [50, 90],
            [60, 75], [70, 60], [80, 45], [90, 28], [100, 12],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2290.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_NOTE
        + " Fitted to the 2021-2024 Enyaq 80, before the 2025 facelift raised"
        + " the peak to 175 kW. Tall SUV body, so the speed penalty is steeper"
        + " than the ID.3's on the same platform.",
    },
    {
        "slug": "tesla-model3-rwd",
        "make": "Tesla",
        "model": "Model 3",
        "variant": "RWD (LFP 57.5 kWh)",
        "usable_kwh": 57.5,
        "consumption": {"model": "quadratic", "a_wh_km": 48.0, "b_wh_km_per_kph2": 0.0088},
        "charge_curve": [
            [0, 80], [10, 170], [20, 170], [30, 150], [40, 130], [50, 110],
            [60, 85], [70, 65], [80, 45], [90, 30], [100, 15],
        ],
        "max_dc_kw": 170.0,
        "mass_kg": 1940.0,
        "top_speed_kph": 201.0,
        "source_note": "LFP pack — relatively flat mid-SoC curve; very low consumption. "
        "Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-model3-lr",
        "make": "Tesla",
        "model": "Model 3",
        "variant": "Long Range 75 kWh",
        "usable_kwh": 75.0,
        "consumption": {"model": "quadratic", "a_wh_km": 50.0, "b_wh_km_per_kph2": 0.0092},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2010.0,
        "top_speed_kph": 201.0,
        "source_note": "V3 Supercharger peak 250 kW at low SoC with steady taper. "
        "Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-model3-performance",
        "make": "Tesla",
        "model": "Model 3",
        "variant": "Performance 79 kWh",
        "usable_kwh": 79.0,
        "consumption": {"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0099},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 1980.0,
        "top_speed_kph": 262.0,
        "source_note": "Long Range pack, stickier tyres and more mass — the same "
        "charging with a consumption penalty that grows with speed. Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-modely-rwd",
        "make": "Tesla",
        "model": "Model Y",
        "variant": "RWD (LFP 57.5 kWh)",
        "usable_kwh": 57.5,
        "consumption": {"model": "quadratic", "a_wh_km": 53.0, "b_wh_km_per_kph2": 0.0096},
        "charge_curve": [
            [0, 80], [10, 170], [20, 170], [30, 150], [40, 130], [50, 110],
            [60, 85], [70, 65], [80, 45], [90, 30], [100, 15],
        ],
        "max_dc_kw": 170.0,
        "mass_kg": 1910.0,
        "top_speed_kph": 217.0,
        "source_note": "The volume Model Y: LFP pack, flat mid-SoC curve, and the "
        "smallest battery of any Tesla here — it stops often. Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-modely-lr",
        "make": "Tesla",
        "model": "Model Y",
        "variant": "Long Range AWD 75 kWh",
        "usable_kwh": 75.0,
        "consumption": {"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0100},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2180.0,
        "top_speed_kph": 217.0,
        "source_note": "Model 3 Long Range pack and curve in a taller body — same "
        "charging, noticeably more drag. Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-modely-performance",
        "make": "Tesla",
        "model": "Model Y",
        "variant": "Performance 79 kWh",
        "usable_kwh": 79.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0107},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2140.0,
        "top_speed_kph": 250.0,
        "source_note": "Curated from Fastned/InsideEVs tests of the 2023 Model Y"
        " Performance. Same 250 kW peak as the Long Range, but the wider"
        " tyres and lowered ride cost it noticeably more per km at 130.",
    },
    {
        "slug": "tesla-models-lr",
        "make": "Tesla",
        "model": "Model S",
        "variant": "Long Range 95 kWh",
        "usable_kwh": 95.0,
        "consumption": {"model": "quadratic", "a_wh_km": 52.0, "b_wh_km_per_kph2": 0.0089},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2110.0,
        "top_speed_kph": 250.0,
        "source_note": "Big pack, low drag: one of the few cars here that can hold a "
        "high cruise without the stops multiplying. Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-models-plaid",
        "make": "Tesla",
        "model": "Model S",
        "variant": "Plaid 95 kWh",
        "usable_kwh": 95.0,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0097},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2190.0,
        "top_speed_kph": 262.0,
        "source_note": "Fast enough to run off the top of the sweep — the answer is "
        "usually still a long way below what it will do. Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-modelx-lr",
        "make": "Tesla",
        "model": "Model X",
        "variant": "Long Range 95 kWh",
        "usable_kwh": 95.0,
        "consumption": {"model": "quadratic", "a_wh_km": 68.0, "b_wh_km_per_kph2": 0.0122},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2455.0,
        "top_speed_kph": 250.0,
        "source_note": "Model S pack in a much bigger body — the drag difference is "
        "the whole story between the two. Curated from Fastned/InsideEVs tests.",
    },
    {
        "slug": "tesla-modelx-plaid",
        "make": "Tesla",
        "model": "Model X",
        "variant": "Plaid 95 kWh",
        "usable_kwh": 95.0,
        "consumption": {"model": "quadratic", "a_wh_km": 72.0, "b_wh_km_per_kph2": 0.0129},
        "charge_curve": [
            [0, 90], [10, 250], [20, 210], [30, 180], [40, 150], [50, 120],
            [60, 95], [70, 75], [80, 55], [90, 35], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 2535.0,
        "top_speed_kph": 262.0,
        "source_note": "Curated from Fastned/InsideEVs tests of the 2023 Model X"
        " Plaid. Two and a half tonnes behind the largest frontal area in the"
        " Tesla range, which is what the quadratic term here is really saying.",
    },
    {
        "slug": "tesla-cybertruck-awd",
        "make": "Tesla",
        "model": "Cybertruck",
        "variant": "AWD 123 kWh",
        "usable_kwh": 123.0,
        "consumption": {"model": "quadratic", "a_wh_km": 100.0, "b_wh_km_per_kph2": 0.0172},
        "charge_curve": [
            [0, 130], [5, 230], [10, 250], [20, 245], [30, 220], [40, 190],
            [50, 158], [60, 128], [70, 97], [80, 68], [90, 37], [100, 15],
        ],
        "max_dc_kw": 250.0,
        "mass_kg": 3100.0,
        "top_speed_kph": 180.0,
        "source_note": _TRUCK_NOTE
        + " An 800 V pack on a truck-shaped brick — fast charging fighting bad "
        "aerodynamics.",
    },
    {
        "slug": "bmw-i4-edrive40",
        "make": "BMW",
        "model": "i4",
        "variant": "eDrive40 84 kWh",
        "usable_kwh": 81.3,
        "consumption": {"model": "quadratic", "a_wh_km": 58.0, "b_wh_km_per_kph2": 0.0100},
        "charge_curve": [
            [0, 85], [10, 200], [20, 205], [35, 180], [45, 150], [55, 120],
            [65, 95], [75, 70], [85, 45], [95, 22], [100, 10],
        ],
        "max_dc_kw": 205.0,
        "mass_kg": 2305.0,
        "top_speed_kph": 190.0,
        "source_note": "Big 81 kWh usable pack with a broad 205 kW plateau — long legs "
        "and few stops. Curated from Fastned model guide + P3 Charging Index style tests.",
    },
    {
        "slug": "volvo-ex30-er",
        "make": "Volvo",
        "model": "EX30",
        "variant": "Single Motor Extended Range",
        "usable_kwh": 64.0,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0115},
        "charge_curve": [
            [0, 70], [5, 145], [10, 153], [25, 150], [35, 125], [45, 105],
            [55, 85], [65, 68], [75, 50], [85, 33], [95, 18], [100, 10],
        ],
        "max_dc_kw": 153.0,
        "mass_kg": 2030.0,
        "top_speed_kph": 180.0,
        "source_note": "Smaller 69 kWh NMC pack (~64 kWh usable), 153 kW peak. The "
        "180 km/h Volvo limiter caps the sweep well before the curve turns. "
        "Curated from Fastned model guide + P3 Charging Index style tests.",
    },
    {
        "slug": "volvo-ex40-er",
        "make": "Volvo",
        "model": "EX40",
        "variant": "Single Motor Extended Range",
        "usable_kwh": 79.0,
        "consumption": {"model": "quadratic", "a_wh_km": 64.0, "b_wh_km_per_kph2": 0.0150},
        "charge_curve": [
            [0, 90], [5, 190], [10, 200], [20, 190], [30, 160], [40, 135],
            [50, 110], [60, 90], [70, 70], [80, 50], [90, 30], [100, 12],
        ],
        "max_dc_kw": 200.0,
        "mass_kg": 2230.0,
        "top_speed_kph": 180.0,
        "source_note": _VOLVO_CMA_NOTE
        + " Formerly the XC40 Recharge; a boxy Cd 0.34 SUV, so motorway speed costs "
        "it more than almost anything else here.",
    },
    {
        "slug": "volvo-ec40-er",
        "make": "Volvo",
        "model": "EC40",
        "variant": "Single Motor Extended Range",
        "usable_kwh": 79.0,
        "consumption": {"model": "quadratic", "a_wh_km": 63.0, "b_wh_km_per_kph2": 0.0141},
        "charge_curve": [
            [0, 90], [5, 190], [10, 200], [20, 190], [30, 160], [40, 135],
            [50, 110], [60, 90], [70, 70], [80, 50], [90, 30], [100, 12],
        ],
        "max_dc_kw": 200.0,
        "mass_kg": 2230.0,
        "top_speed_kph": 180.0,
        "source_note": _VOLVO_CMA_NOTE
        + " Formerly the C40 Recharge — same pack and curve as the EX40, with a "
        "fastback roof worth a few percent of drag.",
    },
    {
        "slug": "polestar-2-lr-sm",
        "make": "Polestar",
        "model": "2",
        "variant": "Long Range Single Motor",
        "usable_kwh": 79.0,
        "consumption": {"model": "quadratic", "a_wh_km": 58.0, "b_wh_km_per_kph2": 0.0115},
        "charge_curve": [
            [0, 85], [10, 200], [20, 205], [30, 175], [40, 145], [50, 115],
            [60, 92], [70, 72], [80, 50], [90, 30], [100, 12],
        ],
        "max_dc_kw": 205.0,
        "mass_kg": 2230.0,
        "top_speed_kph": 205.0,
        "source_note": "2024 rear-motor 82 kWh car: 205 kW peak, taper from ~25% SoC. "
        "Shares the EX40's platform but is far lower and slipperier. "
        "Curated from Fastned model guide + P3 Charging Index style tests.",
    },
    {
        "slug": "renault-megane-60",
        "make": "Renault",
        "model": "Mégane E-Tech",
        "variant": "EV60",
        "usable_kwh": 60.0,
        "consumption": {"model": "quadratic", "a_wh_km": 54.0, "b_wh_km_per_kph2": 0.0102},
        "charge_curve": [
            [0, 45], [10, 130], [30, 125], [40, 105], [50, 90], [60, 70],
            [70, 55], [80, 40], [90, 25], [100, 10],
        ],
        "max_dc_kw": 130.0,
        "mass_kg": 1890.0,
        "top_speed_kph": 160.0,
        "source_note": "Curated from Fastned model guide; modest 130 kW peak with early taper.",
    },
    {
        "slug": "renault-5-52",
        "make": "Renault",
        "model": "5 E-Tech",
        "variant": "Comfort Range 52 kWh",
        "usable_kwh": 52.0,
        "consumption": {"model": "quadratic", "a_wh_km": 48.0, "b_wh_km_per_kph2": 0.0100},
        "charge_curve": [
            [0, 55], [5, 95], [10, 100], [35, 100], [45, 85], [55, 70],
            [65, 58], [75, 45], [85, 30], [95, 16], [100, 8],
        ],
        "max_dc_kw": 100.0,
        "mass_kg": 1630.0,
        "top_speed_kph": 150.0,
        "source_note": "Light (~1.45 t) but not slippery, and only 100 kW peak on a "
        "52 kWh pack — the small-battery, modest-charging case this tool is most "
        "worth running. Limited to 150 km/h, so the sweep stops there. Curated from "
        "Fastned model guide + P3 Charging Index style tests.",
    },
    {
        "slug": "hyundai-ioniq5-73",
        "make": "Hyundai",
        "model": "Ioniq 5",
        "variant": "73 kWh AWD",
        "usable_kwh": 72.6,
        "consumption": {"model": "quadratic", "a_wh_km": 62.0, "b_wh_km_per_kph2": 0.0128},
        "charge_curve": [
            [0, 70], [10, 220], [45, 220], [55, 180], [65, 120], [75, 80],
            [80, 60], [90, 35], [100, 10],
        ],
        "max_dc_kw": 220.0,
        "mass_kg": 2280.0,
        "top_speed_kph": 185.0,
        "source_note": "800 V platform — ~220 kW held nearly flat to ~45-50% SoC, then "
        "steep taper. Curated from Fastned/P3 tests.",
    },
    {
        "slug": "hyundai-ioniq6-74-rwd",
        "make": "Hyundai",
        "model": "Ioniq 6",
        "variant": "77 kWh RWD",
        "usable_kwh": 74.0,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0084},
        "charge_curve": [
            [0, 75], [10, 233], [45, 225], [55, 185], [65, 125], [75, 82],
            [80, 62], [90, 36], [100, 10],
        ],
        "max_dc_kw": 233.0,
        "mass_kg": 2110.0,
        "top_speed_kph": 185.0,
        "source_note": _EGMP_NOTE
        + " Cd 0.21 — the slipperiest car in this list, so the speed penalty bites "
        "later than on the Ioniq 5 it shares a pack with.",
    },
    {
        "slug": "kia-ev6-74-rwd",
        "make": "Kia",
        "model": "EV6",
        "variant": "77 kWh RWD",
        "usable_kwh": 74.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0119},
        "charge_curve": [
            [0, 75], [10, 240], [45, 230], [55, 185], [65, 125], [75, 82],
            [80, 60], [90, 35], [100, 10],
        ],
        "max_dc_kw": 240.0,
        "mass_kg": 2195.0,
        "top_speed_kph": 185.0,
        "source_note": _EGMP_NOTE,
    },
    {
        "slug": "byd-atto3-60",
        "make": "BYD",
        "model": "Atto 3",
        "variant": "Comfort 60 kWh",
        "usable_kwh": 60.5,
        "consumption": {"model": "quadratic", "a_wh_km": 62.0, "b_wh_km_per_kph2": 0.0126},
        "charge_curve": [
            [0, 60], [5, 80], [10, 88], [20, 88], [35, 85], [45, 78],
            [55, 68], [65, 58], [75, 45], [85, 30], [95, 15], [100, 8],
        ],
        "max_dc_kw": 88.0,
        "mass_kg": 1930.0,
        "top_speed_kph": 160.0,
        "source_note": _BYD_BLADE_NOTE
        + " Boxy for an SUV of its size, so motorway consumption climbs fast — "
        "the combination this tool exists to price.",
    },
    {
        "slug": "byd-dolphin-60",
        "make": "BYD",
        "model": "Dolphin",
        "variant": "Design 60 kWh",
        "usable_kwh": 60.4,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0109},
        "charge_curve": [
            [0, 58], [5, 80], [10, 88], [20, 88], [35, 84], [45, 76],
            [55, 66], [65, 56], [75, 44], [85, 29], [95, 15], [100, 8],
        ],
        "max_dc_kw": 88.0,
        "mass_kg": 1838.0,
        "top_speed_kph": 160.0,
        "source_note": _BYD_BLADE_NOTE
        + " Lighter and slipperier than the Atto 3 on the same pack and the same "
        "modest 88 kW ceiling.",
    },
    {
        "slug": "byd-seal-82-rwd",
        "make": "BYD",
        "model": "Seal",
        "variant": "Design RWD 82 kWh",
        "usable_kwh": 82.5,
        "consumption": {"model": "quadratic", "a_wh_km": 58.0, "b_wh_km_per_kph2": 0.0099},
        "charge_curve": [
            [0, 80], [5, 140], [10, 150], [25, 150], [35, 140], [45, 120],
            [55, 100], [65, 80], [75, 60], [85, 38], [95, 18], [100, 8],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2065.0,
        "top_speed_kph": 180.0,
        "source_note": _BYD_BLADE_NOTE
        + " The saloon is the aerodynamic one of the range (Cd ~0.22) and the only "
        "BYD here with a three-figure charge plateau.",
    },
    {
        "slug": "byd-seal-u-72",
        "make": "BYD",
        "model": "Seal U",
        "variant": "Comfort 72 kWh",
        "usable_kwh": 71.8,
        "consumption": {"model": "quadratic", "a_wh_km": 64.0, "b_wh_km_per_kph2": 0.0122},
        "charge_curve": [
            [0, 65], [5, 105], [10, 115], [25, 115], [35, 105], [45, 92],
            [55, 78], [65, 62], [75, 48], [85, 30], [95, 15], [100, 8],
        ],
        "max_dc_kw": 115.0,
        "mass_kg": 2150.0,
        "top_speed_kph": 175.0,
        "source_note": _BYD_BLADE_NOTE
        + " Family-SUV shape on a 72 kWh pack: the extra frontal area costs more at "
        "150 than the bigger battery buys back.",
    },
    {
        "slug": "byd-sealion7-82",
        "make": "BYD",
        "model": "Sealion 7",
        "variant": "Comfort RWD 82 kWh",
        "usable_kwh": 82.5,
        "consumption": {"model": "quadratic", "a_wh_km": 64.0, "b_wh_km_per_kph2": 0.0116},
        "charge_curve": [
            [0, 80], [5, 140], [10, 150], [25, 150], [35, 138], [45, 118],
            [55, 98], [65, 78], [75, 58], [85, 37], [95, 18], [100, 8],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2405.0,
        "top_speed_kph": 190.0,
        "source_note": _BYD_BLADE_NOTE
        + " Seal running gear in a heavier coupé-SUV body — same charge ceiling, "
        "noticeably more energy per kilometre.",
    },
    {
        "slug": "mg4-74-er",
        "make": "MG",
        "model": "MG4",
        "variant": "Extended Range 77 kWh",
        "usable_kwh": 74.4,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0106},
        "charge_curve": [
            [0, 70], [5, 130], [10, 144], [20, 144], [30, 130], [40, 112],
            [50, 95], [60, 78], [70, 60], [80, 44], [90, 25], [100, 10],
        ],
        "max_dc_kw": 144.0,
        "mass_kg": 1985.0,
        "top_speed_kph": 160.0,
        "source_note": _CHINA_CAVEAT
        + " SAIC's NMC pack, so a conventional peak-then-taper curve rather than the "
        "flat LFP shape. Limited to 160 km/h.",
    },
    {
        "slug": "xpeng-g6-88",
        "make": "XPeng",
        "model": "G6",
        "variant": "Long Range 88 kWh",
        "usable_kwh": 87.5,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0104},
        "charge_curve": [
            [0, 120], [5, 240], [10, 280], [20, 280], [30, 250], [40, 210],
            [50, 170], [60, 135], [70, 100], [80, 70], [90, 38], [100, 15],
        ],
        "max_dc_kw": 280.0,
        "mass_kg": 2180.0,
        "top_speed_kph": 200.0,
        "source_note": _CHINA_CAVEAT
        + " 800 V pack and the fastest charger in this catalog — the case where "
        "driving hard genuinely pays, because the stops are short enough to absorb "
        "the extra energy.",
    },
    {
        "slug": "nio-et5-75",
        "make": "NIO",
        "model": "ET5",
        "variant": "Standard Range 75 kWh",
        "usable_kwh": 75.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0101},
        "charge_curve": [
            [0, 70], [5, 125], [10, 140], [25, 140], [35, 125], [45, 108],
            [55, 90], [65, 72], [75, 55], [85, 35], [95, 16], [100, 8],
        ],
        "max_dc_kw": 140.0,
        "mass_kg": 2345.0,
        "top_speed_kph": 200.0,
        "source_note": _CHINA_CAVEAT
        + " Slippery (Cd ~0.24) but heavy, on a middling 140 kW ceiling. Battery "
        "swapping is NOT modelled — this is the plug-in-and-wait answer.",
    },
    {
        "slug": "zeekr-x-64",
        "make": "Zeekr",
        "model": "X",
        "variant": "Long Range RWD 66 kWh",
        "usable_kwh": 64.0,
        "consumption": {"model": "quadratic", "a_wh_km": 62.0, "b_wh_km_per_kph2": 0.0117},
        "charge_curve": [
            [0, 70], [5, 135], [10, 150], [20, 150], [30, 135], [40, 115],
            [50, 95], [60, 76], [70, 58], [80, 42], [90, 24], [100, 10],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2170.0,
        "top_speed_kph": 190.0,
        "source_note": _CHINA_CAVEAT
        + " Geely SEA platform, same family as the Volvo EX30 already in this list — "
        "a useful side-by-side.",
    },
    {
        "slug": "leapmotor-c10-70",
        "make": "Leapmotor",
        "model": "C10",
        "variant": "Design 70 kWh",
        "usable_kwh": 69.9,
        "consumption": {"model": "quadratic", "a_wh_km": 62.0, "b_wh_km_per_kph2": 0.0117},
        "charge_curve": [
            [0, 55], [5, 78], [10, 84], [25, 84], [35, 80], [45, 72],
            [55, 62], [65, 52], [75, 40], [85, 26], [95, 13], [100, 6],
        ],
        "max_dc_kw": 84.0,
        "mass_kg": 2160.0,
        "top_speed_kph": 170.0,
        "source_note": _CHINA_CAVEAT
        + " The slowest charger here by some margin: a 70 kWh SUV that only takes "
        "~84 kW, so every stop is long and the optimum speed drops accordingly.",
    },
    {
        "slug": "dongfeng-box-42",
        "make": "Dongfeng",
        "model": "Box",
        "variant": "42 kWh",
        "usable_kwh": 42.3,
        "consumption": {"model": "quadratic", "a_wh_km": 50.0, "b_wh_km_per_kph2": 0.0101},
        "charge_curve": [
            [0, 55], [5, 80], [10, 90], [20, 88], [30, 80], [40, 70],
            [50, 58], [60, 48], [70, 38], [80, 26], [90, 15], [100, 7],
        ],
        "max_dc_kw": 90.0,
        "mass_kg": 1660.0,
        "top_speed_kph": 150.0,
        "source_note": _CHINA_CAVEAT
        + " A 42 kWh city car taken on a holiday run: limited to 150 km/h, and the "
        "small pack means the stops come round often however gently you drive. "
        "Expect the sweep to bottom out low.",
    },
    {
        "slug": "ford-mustang-mach-e-er",
        "make": "Ford",
        "model": "Mustang Mach-E",
        "variant": "Extended Range RWD 91 kWh",
        "usable_kwh": 91.0,
        "consumption": {"model": "quadratic", "a_wh_km": 72.0, "b_wh_km_per_kph2": 0.0128},
        "charge_curve": [
            [0, 100], [5, 145], [10, 150], [20, 145], [30, 130], [40, 112],
            [50, 92], [60, 74], [70, 56], [80, 40], [90, 22], [100, 9],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2200.0,
        "top_speed_kph": 180.0,
        "source_note": _US_NOTE
        + " Fitted to the 2023 Extended Range RWD. A modest 150 kW peak that"
        + " holds unusually flat, so it rewards longer stops than its rivals.",
    },
    {
        "slug": "ford-f150-lightning-er",
        "make": "Ford",
        "model": "F-150 Lightning",
        "variant": "Extended Range 131 kWh",
        "usable_kwh": 131.0,
        "consumption": {"model": "quadratic", "a_wh_km": 118.0, "b_wh_km_per_kph2": 0.0205},
        "charge_curve": [
            [0, 90], [5, 150], [10, 155], [20, 150], [30, 135], [40, 120],
            [50, 100], [60, 82], [70, 62], [80, 45], [90, 25], [100, 10],
        ],
        "max_dc_kw": 155.0,
        "mass_kg": 2950.0,
        "top_speed_kph": 180.0,
        "source_note": _TRUCK_NOTE
        + " America's best-selling vehicle in electric form, and the clearest "
        "case in the catalog for slowing down: a huge pack that charges no "
        "faster than a VW ID.4.",
    },
    {
        "slug": "chevrolet-bolt-euv",
        "make": "Chevrolet",
        "model": "Bolt EUV",
        "variant": "65 kWh",
        "usable_kwh": 65.0,
        "consumption": {"model": "quadratic", "a_wh_km": 63.0, "b_wh_km_per_kph2": 0.0121},
        "charge_curve": [
            [0, 40], [5, 54], [10, 55], [20, 55], [30, 55], [40, 55],
            [50, 50], [60, 44], [70, 36], [80, 27], [90, 16], [100, 7],
        ],
        "max_dc_kw": 55.0,
        "mass_kg": 1680.0,
        "top_speed_kph": 145.0,
        "source_note": _US_NOTE
        + " The slowest DC charging in this catalog by a wide margin — 55 kW flat. "
        "On a long route it is the extreme case of the whole thesis: driving "
        "faster buys stops it can never win back.",
    },
    {
        "slug": "chevrolet-equinox-ev",
        "make": "Chevrolet",
        "model": "Equinox EV",
        "variant": "LT FWD 85 kWh",
        "usable_kwh": 85.0,
        "consumption": {"model": "quadratic", "a_wh_km": 70.0, "b_wh_km_per_kph2": 0.0126},
        "charge_curve": [
            [0, 100], [5, 150], [10, 150], [20, 145], [30, 130], [40, 112],
            [50, 92], [60, 74], [70, 56], [80, 40], [90, 22], [100, 9],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2200.0,
        "top_speed_kph": 160.0,
        "source_note": _US_NOTE + " GM's Ultium platform at the volume end.",
    },
    {
        "slug": "rivian-r1t-large",
        "make": "Rivian",
        "model": "R1T",
        "variant": "Large Pack AWD 135 kWh",
        "usable_kwh": 135.0,
        "consumption": {"model": "quadratic", "a_wh_km": 108.0, "b_wh_km_per_kph2": 0.0186},
        "charge_curve": [
            [0, 120], [5, 200], [10, 215], [20, 210], [30, 190], [40, 165],
            [50, 138], [60, 112], [70, 85], [80, 60], [90, 33], [100, 13],
        ],
        "max_dc_kw": 215.0,
        "mass_kg": 3130.0,
        "top_speed_kph": 180.0,
        "source_note": _TRUCK_NOTE
        + " Charges far harder than the Lightning, which is what makes the pair "
        "worth comparing on the same route.",
    },
    {
        "slug": "lucid-air-grand-touring",
        "make": "Lucid",
        "model": "Air",
        "variant": "Grand Touring 112 kWh",
        "usable_kwh": 112.0,
        "consumption": {"model": "quadratic", "a_wh_km": 52.0, "b_wh_km_per_kph2": 0.0092},
        "charge_curve": [
            [0, 130], [5, 250], [10, 300], [20, 290], [30, 260], [40, 225],
            [50, 185], [60, 148], [70, 112], [80, 78], [90, 42], [100, 16],
        ],
        "max_dc_kw": 300.0,
        "mass_kg": 2360.0,
        "top_speed_kph": 270.0,
        "source_note": _US_NOTE
        + " A 900 V pack and the most efficient car here: the combination that "
        "pushes the optimum cruise speed to the top of the sweep.",
    },
    {
        "slug": "cadillac-lyriq",
        "make": "Cadillac",
        "model": "Lyriq",
        "variant": "RWD 102 kWh",
        "usable_kwh": 102.0,
        "consumption": {"model": "quadratic", "a_wh_km": 78.0, "b_wh_km_per_kph2": 0.0138},
        "charge_curve": [
            [0, 120], [5, 185], [10, 190], [20, 185], [30, 165], [40, 142],
            [50, 118], [60, 95], [70, 72], [80, 50], [90, 28], [100, 11],
        ],
        "max_dc_kw": 190.0,
        "mass_kg": 2650.0,
        "top_speed_kph": 210.0,
        "source_note": _US_NOTE
        + " Fitted to the 2024 Lyriq RWD on GM's Ultium pack: a 190 kW peak on"
        + " a 102 kWh pack, so it gains SoC slowly in percentage terms.",
    },
]

# `sort_order` is derived from position in the list above rather than written
# into each entry, so the file's order *is* the catalog's order: the picker
# renders in it, and `/ev` derives its make sections from it. Moving a car means
# moving its dict, not renumbering its neighbours — which is what the old
# hand-kept scheme cost, and why it had already drifted (the Cybertruck sat 90
# numbers away from the other Teslas).
#
# The step of 10 leaves room to hand-place a car between two others in a
# migration if that is ever needed. Mutating in place keeps `VEHICLES` a plain
# list of complete dicts, which is what `seed()` and the tests both expect.
for _i, _car in enumerate(VEHICLES):
    _car["sort_order"] = (_i + 1) * 10
del _i, _car


def seed(session: Session) -> tuple[int, int]:
    """Upsert all curated vehicles by slug. Returns (created, updated)."""
    created = updated = 0
    for data in VEHICLES:
        existing = session.execute(
            select(Vehicle).where(Vehicle.slug == data["slug"])
        ).scalar_one_or_none()
        if existing is None:
            session.add(Vehicle(**data))
            created += 1
        else:
            for key, value in data.items():
                setattr(existing, key, value)
            updated += 1
    session.commit()
    return created, updated


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="seed_vehicles: %(message)s")
    engine = get_sync_engine()
    try:
        with Session(engine) as session:
            created, updated = seed(session)
        logger.info("Seeded vehicles: %d created, %d updated", created, updated)
        return 0
    except Exception as exc:
        logger.exception("Seeding failed: %s", exc)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
