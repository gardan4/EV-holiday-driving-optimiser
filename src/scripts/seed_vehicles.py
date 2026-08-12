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

_MEB_2025_NOTE = (
    "VW MEB, 2025-on pack and rear motor: a taller peak than the 120/135 kW "
    "generation and a much later taper, so these want a single long stop where "
    "the older MEB cars wanted two short ones. Curve fitted to the 10-80% time "
    "and average power published by EV Database, cross-checked against the "
    "manufacturer's own charging figures."
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
        "nameplate_slug": "vw-id3",
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
        "slug": "vw-id3-pro-s-79",
        "nameplate_slug": "vw-id3",
        "make": "Volkswagen",
        "model": "ID.3",
        "variant": "Pro S 79 kWh (2025 on)",
        "usable_kwh": 79.0,
        "consumption": {"model": "quadratic", "a_wh_km": 57.0, "b_wh_km_per_kph2": 0.0108},
        "charge_curve": [
            [0, 78], [5, 176], [10, 185], [40, 185], [50, 141], [60, 107],
            [70, 83], [80, 63], [90, 44], [100, 15],
        ],
        "max_dc_kw": 185.0,
        "mass_kg": 2137.0,
        "top_speed_kph": 160.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the MY26 Pro S: 185 kW, the highest peak of any MEB car"
        + " here, and the 160 km/h limiter the whole ID.3 range shares.",
    },
    {
        "slug": "vw-id4-pro-77",
        "nameplate_slug": "vw-id4",
        "make": "Volkswagen",
        "model": "ID.4",
        "variant": "Pro 77 kWh (2021-2024, 135 kW)",
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
        "slug": "vw-id4-pro-2025",
        "nameplate_slug": "vw-id4",
        "make": "Volkswagen",
        "model": "ID.4",
        "variant": "Pro 77 kWh (2025 on, 175 kW)",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 59.0, "b_wh_km_per_kph2": 0.0123},
        "charge_curve": [
            [0, 74], [5, 166], [10, 175], [37, 175], [47, 133], [57, 102],
            [67, 79], [77, 60], [87, 42], [97, 28], [100, 14],
        ],
        "max_dc_kw": 175.0,
        "mass_kg": 2319.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the MY26 ID.4 Pro. Same 77 kWh as the 2021-2024 car above"
        + " and nine minutes quicker from 10 to 80%, which is the whole point of"
        + " the update and the reason both are listed.",
    },
    {
        "slug": "vw-id7-pro-77",
        "nameplate_slug": "vw-id7",
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
        "slug": "vw-id7-pro-s-86",
        "nameplate_slug": "vw-id7",
        "make": "Volkswagen",
        "model": "ID.7",
        "variant": "Pro S 86 kWh",
        "usable_kwh": 86.0,
        "consumption": {"model": "quadratic", "a_wh_km": 61.0, "b_wh_km_per_kph2": 0.0106},
        "charge_curve": [
            [0, 84], [5, 190], [10, 200], [40, 200], [50, 152], [60, 116],
            [70, 90], [80, 68], [90, 48], [100, 16],
        ],
        "max_dc_kw": 200.0,
        "mass_kg": 2406.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the MY26 ID.7 Pro S. The 86 kWh pack takes 200 kW, the"
        + " highest in the MEB range, and the Cd 0.23 saloon body means it is"
        + " also the one that wastes least of it at 130 km/h.",
    },
    {
        "slug": "vw-idbuzz-lwb-86",
        "nameplate_slug": "vw-idbuzz",
        "make": "Volkswagen",
        "model": "ID. Buzz",
        "variant": "LWB Pro 86 kWh",
        "usable_kwh": 86.0,
        "consumption": {"model": "quadratic", "a_wh_km": 70.0, "b_wh_km_per_kph2": 0.017},
        "charge_curve": [
            [0, 84], [5, 190], [10, 200], [40, 200], [50, 152], [60, 116],
            [70, 90], [80, 68], [90, 48], [100, 16],
        ],
        "max_dc_kw": 200.0,
        "mass_kg": 2808.0,
        "top_speed_kph": 160.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the LWB Pro, which shares the ID.7 Pro S's 86 kWh pack,"
        + " 200 kW peak and 26-minute 10-80% exactly — and then has to push a"
        + " van through the air. Its quadratic term is the highest of any"
        + " European car here, so this is the entry where cruise speed costs"
        + " the most and the sweep has the most to say.",
    },
    {
        "slug": "cupra-born-58",
        "nameplate_slug": "cupra-born",
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
        "nameplate_slug": "cupra-born",
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
        "slug": "cupra-tavascan-77",
        "nameplate_slug": "cupra-tavascan",
        "make": "Cupra",
        "model": "Tavascan",
        "variant": "VZ 77 kWh",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0131},
        "charge_curve": [
            [0, 57], [5, 128], [10, 135], [53, 135], [63, 103], [73, 78],
            [83, 61], [93, 46], [100, 11],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2453.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the 2024-2026 Tavascan VZ: the 135 kW peak held past 50%"
        + " SoC, like the Enyaq 85. At nearly 2.3 tonnes before passengers it is"
        + " the heaviest MEB car here, and the quadratic term shows it.",
    },
    {
        "slug": "skoda-enyaq-77",
        "nameplate_slug": "skoda-enyaq",
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
        "slug": "skoda-enyaq-85",
        "nameplate_slug": "skoda-enyaq",
        "make": "Škoda",
        "model": "Enyaq",
        "variant": "85 (77 kWh, 2025 on)",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 59.0, "b_wh_km_per_kph2": 0.012},
        "charge_curve": [
            [0, 57], [5, 128], [10, 135], [53, 135], [63, 103], [73, 78],
            [83, 61], [93, 46], [100, 11],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2321.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the MY25 Enyaq 85, which keeps the 135 kW peak of the"
        + " 2021-2024 car and holds it past 50% SoC instead of 30%. That alone"
        + " takes nine minutes off a 10-80% stop, with no peak-power headline.",
    },
    {
        "slug": "skoda-elroq-85",
        "nameplate_slug": "skoda-elroq",
        "make": "Škoda",
        "model": "Elroq",
        "variant": "85 (77 kWh)",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 58.0, "b_wh_km_per_kph2": 0.0121},
        "charge_curve": [
            [0, 69], [5, 157], [10, 165], [37, 165], [47, 125], [57, 96],
            [67, 74], [77, 56], [87, 40], [97, 26], [100, 13],
        ],
        "max_dc_kw": 165.0,
        "mass_kg": 2311.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the MY27 Elroq 85. Škoda's own press kit and EV Database"
        + " disagree on this car's peak (135 vs 165 kW) because they describe"
        + " different model years; the later figure is used, and it is the one"
        + " to re-check first if these numbers ever look wrong.",
    },
    {
        "slug": "audi-q4-45",
        "nameplate_slug": "audi-q4-etron",
        "make": "Audi",
        "model": "Q4 e-tron",
        "variant": "45 (77 kWh)",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 61.0, "b_wh_km_per_kph2": 0.0133},
        "charge_curve": [
            [0, 74], [5, 166], [10, 175], [37, 175], [47, 133], [57, 102],
            [67, 79], [77, 60], [87, 42], [97, 28], [100, 14],
        ],
        "max_dc_kw": 175.0,
        "mass_kg": 2325.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_2025_NOTE
        + " Fitted to the MY24-26 Q4 45, which shares the ID.4 Pro's pack and"
        + " curve exactly. What differs is the body: it is the least slippery"
        + " car on this platform, so it pays more per kilometre for the same"
        + " charge.",
    },
    {
        "slug": "audi-q6-etron",
        "nameplate_slug": "audi-q6-etron",
        "make": "Audi",
        "model": "Q6 e-tron",
        "variant": "performance 95 kWh",
        "usable_kwh": 94.9,
        "consumption": {"model": "quadratic", "a_wh_km": 64.0, "b_wh_km_per_kph2": 0.0138},
        "charge_curve": [
            [0, 94], [5, 247], [10, 269], [37, 269], [47, 215], [57, 161],
            [67, 118], [77, 83], [87, 54], [97, 32], [100, 16],
        ],
        "max_dc_kw": 269.0,
        "mass_kg": 2455.0,
        "top_speed_kph": 209.0,
        "source_note": "Audi Q6 e-tron performance (MY24) on the 800 V PPE platform — a "
        "different machine from the MEB Q4 above it: 269 kW held to nearly 40% "
        "SoC, 23 minutes for 10-80% on a 95 kWh pack. It is also one of the "
        "few cars here whose 209 km/h limiter is above the top of the sweep. "
        "Curve fitted to EV Database's published 10-80% time.",
    },
    {
        "slug": "tesla-model3-rwd",
        "nameplate_slug": "tesla-model-3",
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
        "nameplate_slug": "tesla-model-3",
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
        "nameplate_slug": "tesla-model-3",
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
        "nameplate_slug": "tesla-model-y",
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
        "nameplate_slug": "tesla-model-y",
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
        "nameplate_slug": "tesla-model-y",
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
        "nameplate_slug": "tesla-model-s",
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
        "nameplate_slug": "tesla-model-s",
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
        "nameplate_slug": "tesla-model-x",
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
        "nameplate_slug": "tesla-model-x",
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
        "nameplate_slug": "tesla-cybertruck",
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
        "nameplate_slug": "bmw-i4",
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
        "slug": "bmw-ix1-edrive20",
        "nameplate_slug": "bmw-ix1",
        "make": "BMW",
        "model": "iX1",
        "variant": "eDrive20 65 kWh",
        "usable_kwh": 65.2,
        "consumption": {"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0117},
        "charge_curve": [
            [0, 55], [5, 124], [10, 130], [42, 130], [52, 99], [62, 75],
            [72, 58], [82, 44], [92, 31], [100, 10],
        ],
        "max_dc_kw": 130.0,
        "mass_kg": 2120.0,
        "top_speed_kph": 170.0,
        "source_note": "BMW iX1 eDrive20 (MY26): 130 kW and thirty minutes for 10-80%, on a "
        "65 kWh pack that is small next to the i4's 84. It is the efficient "
        "one of the pair at motorway speed, which narrows the range gap more "
        "than the pack sizes imply. Curve fitted to EV Database's figures.",
    },
    {
        "slug": "bmw-i5-edrive40",
        "nameplate_slug": "bmw-i5",
        "make": "BMW",
        "model": "i5",
        "variant": "eDrive40 81 kWh",
        "usable_kwh": 81.2,
        "consumption": {"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0114},
        "charge_curve": [
            [0, 87], [5, 196], [10, 206], [32, 206], [42, 157], [52, 119],
            [62, 93], [72, 70], [82, 49], [92, 33], [100, 16],
        ],
        "max_dc_kw": 206.0,
        "mass_kg": 2375.0,
        "top_speed_kph": 193.0,
        "source_note": "BMW i5 eDrive40 Sedan (MY25), and the interesting thing about it is how "
        "little separates it from the i4 above: 81 kWh against 81, 206 kW "
        "against 205, both about half an hour for 10-80%. What differs is the "
        "car around them, and at 130 km/h the bigger i5 uses about 9% more per "
        "kilometre. Same pack, same stop, less range. Curve fitted to EV "
        "Database's published figures.",
    },
    {
        "slug": "mercedes-eqa-250-plus",
        "nameplate_slug": "mercedes-eqa",
        "make": "Mercedes-Benz",
        "model": "EQA",
        "variant": "250+ 70.5 kWh",
        "usable_kwh": 70.5,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0123},
        "charge_curve": [
            [0, 43], [5, 97], [10, 102], [51, 102], [61, 78], [71, 59],
            [81, 46], [91, 35], [100, 8],
        ],
        "max_dc_kw": 102.0,
        "mass_kg": 2230.0,
        "top_speed_kph": 160.0,
        "source_note": "Mercedes EQA 250+ (2023-2026): a 400 V car with the lowest peak of any "
        "European entry here at 102 kW, held past 50% SoC so the 35-minute "
        "10-80% is better than that number suggests. Limited to 160 km/h. "
        "Curve fitted to EV Database's published 10-80% time.",
    },
    {
        "slug": "mercedes-eqb-250-plus",
        "nameplate_slug": "mercedes-eqb",
        "make": "Mercedes-Benz",
        "model": "EQB",
        "variant": "250+ 70.5 kWh",
        "usable_kwh": 70.5,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0136},
        "charge_curve": [
            [0, 43], [5, 97], [10, 102], [51, 102], [61, 78], [71, 59],
            [81, 46], [91, 35], [100, 8],
        ],
        "max_dc_kw": 102.0,
        "mass_kg": 2285.0,
        "top_speed_kph": 160.0,
        "source_note": "Mercedes EQB 250+ (2023-2026): the EQA's pack and charging hardware in "
        "a taller seven-seat body, so the curve here is deliberately identical "
        "and the consumption is not. Published motorway figures give the two "
        "bodies the same number, which cannot be right for a box this much "
        "larger; the quadratic term has been raised for its frontal area, and "
        "that adjustment is ours rather than a measurement.",
    },
    {
        "slug": "mercedes-cla-250-plus",
        "nameplate_slug": "mercedes-cla",
        "make": "Mercedes-Benz",
        "model": "CLA",
        "variant": "Shooting Brake 250+ 85 kWh",
        "usable_kwh": 85.0,
        "consumption": {"model": "quadratic", "a_wh_km": 52.0, "b_wh_km_per_kph2": 0.0098},
        "charge_curve": [
            [0, 106], [5, 318], [10, 353], [35, 353], [45, 261], [55, 191],
            [65, 141], [75, 99], [85, 64], [95, 39], [100, 18],
        ],
        "max_dc_kw": 353.0,
        "mass_kg": 2255.0,
        "top_speed_kph": 209.0,
        "source_note": "Mercedes CLA Shooting Brake 250+ (2025-2026) on the 800 V MMA platform: "
        "the fastest-charging and most efficient car in this catalog, at 18 "
        "minutes for 10-80% and a Cd that puts its quadratic term below every "
        "European entry bar the Ioniq 6. Sources disagree on the peak — "
        "Mercedes markets \"up to 320 kW\" and EV Database measures 353 — and "
        "the higher figure is used here, so treat the very top of the curve as "
        "the least certain part of it. Fitted to the published 10-80% time.",
    },
    {
        "slug": "volvo-ex30-er",
        "nameplate_slug": "volvo-ex30",
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
        "nameplate_slug": "volvo-ex40",
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
        "nameplate_slug": "volvo-ec40",
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
        "slug": "volvo-ex90-88",
        "nameplate_slug": "volvo-ex90",
        "make": "Volvo",
        "model": "EX90",
        "variant": "Single Motor 88 kWh",
        "usable_kwh": 88.0,
        "consumption": {"model": "quadratic", "a_wh_km": 68.0, "b_wh_km_per_kph2": 0.0156},
        "charge_curve": [
            [0, 108], [5, 285], [10, 310], [29, 310], [39, 248], [49, 186],
            [59, 136], [69, 96], [79, 62], [89, 37], [100, 19],
        ],
        "max_dc_kw": 310.0,
        "mass_kg": 2736.0,
        "top_speed_kph": 180.0,
        "source_note": "Volvo EX90 Single Motor (MY26-27): an 800 V car, and nothing like the "
        "CMA-platform EX40 and EC40 above it — 310 kW against their 200, and "
        "23 minutes for 10-80% on a pack half again as big. At 2.7 tonnes it "
        "is the heaviest car Volvo makes. Like every Volvo it is limited "
        "to 180 km/h. Curve fitted to EV Database's published 10-80% time.",
    },
    {
        "slug": "polestar-2-lr-sm",
        "nameplate_slug": "polestar-2",
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
        "slug": "polestar-4-94",
        "nameplate_slug": "polestar-4",
        "make": "Polestar",
        "model": "4",
        "variant": "Long Range Single Motor 94 kWh",
        "usable_kwh": 94.0,
        "consumption": {"model": "quadratic", "a_wh_km": 62.0, "b_wh_km_per_kph2": 0.0141},
        "charge_curve": [
            [0, 84], [5, 190], [10, 200], [36, 200], [46, 152], [56, 116],
            [66, 90], [76, 68], [86, 48], [96, 32], [100, 16],
        ],
        "max_dc_kw": 200.0,
        "mass_kg": 2485.0,
        "top_speed_kph": 200.0,
        "source_note": "Polestar 4 Long Range Single Motor (MY24-26): the biggest pack of the "
        "Nordic cars at 94 kWh, on a 400 V system that peaks at 200 kW, so "
        "10-80% is 31 minutes. It goes further between stops than the EX90 and "
        "takes longer at each one — the trade this app exists to price. Curve "
        "fitted to EV Database's published time.",
    },
    {
        "slug": "renault-megane-60",
        "nameplate_slug": "renault-megane",
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
        "nameplate_slug": "renault-5",
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
        "slug": "renault-scenic-87",
        "nameplate_slug": "renault-scenic",
        "make": "Renault",
        "model": "Scenic E-Tech",
        "variant": "EV87 87 kWh",
        "usable_kwh": 87.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0134},
        "charge_curve": [
            [0, 63], [5, 142], [10, 150], [34, 150], [44, 114], [54, 87],
            [64, 68], [74, 51], [84, 36], [94, 24], [100, 12],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2097.0,
        "top_speed_kph": 170.0,
        "source_note": "Renault Scenic E-Tech EV87 (2025 update): the biggest pack of the "
        "French cars here at 87 kWh, but only 150 kW to fill it, so 10-80% is "
        "forty minutes. It is a car that wants fewer, longer stops than its "
        "range alone suggests. Curve fitted to EV Database's published time.",
    },
    {
        "slug": "peugeot-e3008-73",
        "nameplate_slug": "peugeot-e3008",
        "make": "Peugeot",
        "model": "e-3008",
        "variant": "73 kWh",
        "usable_kwh": 73.0,
        "consumption": {"model": "quadratic", "a_wh_km": 63.0, "b_wh_km_per_kph2": 0.0136},
        "charge_curve": [
            [0, 67], [5, 152], [10, 160], [28, 160], [38, 122], [48, 93],
            [58, 72], [68, 54], [78, 38], [88, 26], [100, 13],
        ],
        "max_dc_kw": 160.0,
        "mass_kg": 2363.0,
        "top_speed_kph": 170.0,
        "source_note": "Peugeot e-3008 73 kWh (2024-2026) on Stellantis' STLA Medium platform, "
        "the first entry here from a group that sells more cars in Europe than "
        "anyone else. A tall 160 kW peak that gives up early — the taper "
        "starts under 30% SoC — so 10-80% still takes thirty-six minutes. "
        "Curve fitted to EV Database's published time.",
    },
    {
        "slug": "peugeot-e5008-73",
        "nameplate_slug": "peugeot-e5008",
        "make": "Peugeot",
        "model": "e-5008",
        "variant": "73 kWh",
        "usable_kwh": 73.0,
        "consumption": {"model": "quadratic", "a_wh_km": 66.0, "b_wh_km_per_kph2": 0.0143},
        "charge_curve": [
            [0, 67], [5, 152], [10, 160], [28, 160], [38, 122], [48, 93],
            [58, 72], [68, 54], [78, 38], [88, 26], [100, 13],
        ],
        "max_dc_kw": 160.0,
        "mass_kg": 2440.0,
        "top_speed_kph": 170.0,
        "source_note": "Peugeot e-5008 73 kWh (2024-2026): the e-3008's pack, motor and curve "
        "in a seven-seat body eighty kilos heavier. It is a separate nameplate "
        "rather than a version of the e-3008 because nobody cross-shops them, "
        "and the published motorway consumption really does separate the two "
        "bodies here. Curve fitted to EV Database's published time.",
    },
    {
        "slug": "hyundai-ioniq5-73",
        "nameplate_slug": "hyundai-ioniq-5",
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
        "slug": "hyundai-ioniq5-84",
        "nameplate_slug": "hyundai-ioniq-5",
        "make": "Hyundai",
        "model": "Ioniq 5",
        "variant": "84 kWh RWD (2024 on)",
        "usable_kwh": 80.0,
        "consumption": {"model": "quadratic", "a_wh_km": 62.0, "b_wh_km_per_kph2": 0.0127},
        "charge_curve": [
            [0, 79], [10, 263], [45, 263], [55, 208], [65, 139], [75, 92],
            [80, 68], [90, 39], [100, 11],
        ],
        "max_dc_kw": 263.0,
        "mass_kg": 2240.0,
        "top_speed_kph": 185.0,
        "source_note": _EGMP_NOTE
        + " Fitted to the MY24 84 kWh RWD, the fastest-charging car in this"
        + " catalog: 263 kW held to 45% SoC puts 10-80% at eighteen minutes."
        + " On a long drive that is worth more than its extra range.",
    },
    {
        "slug": "hyundai-ioniq6-74-rwd",
        "nameplate_slug": "hyundai-ioniq-6",
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
        "slug": "hyundai-kona-64",
        "nameplate_slug": "hyundai-kona",
        "make": "Hyundai",
        "model": "Kona Electric",
        "variant": "64 kWh (2018-2023)",
        "usable_kwh": 64.0,
        "consumption": {"model": "quadratic", "a_wh_km": 56.0, "b_wh_km_per_kph2": 0.0120},
        "charge_curve": [
            [0, 34], [5, 70], [10, 77], [40, 77], [50, 58], [62, 50],
            [72, 41], [80, 34], [90, 23], [100, 8],
        ],
        "max_dc_kw": 77.0,
        "mass_kg": 1940.0,
        "top_speed_kph": 167.0,
        "source_note": "Hyundai Kona Electric, first generation (2018-2023): the same name as "
        "the 2024 car and a different one to plan around — 77 kW peak against "
        "105, so 10-80% takes forty-six minutes instead of thirty-seven, and a "
        "long day out needs one more stop or one longer one. Marginally the "
        "more efficient of the two at a cruise. Charging hardware did not change "
        "across MY19, MY21 and MY22 facelifts, so one curve covers the "
        "generation; fitted to EV Database's published figures.",
    },
    {
        "slug": "hyundai-kona-65",
        "nameplate_slug": "hyundai-kona",
        "make": "Hyundai",
        "model": "Kona Electric",
        "variant": "65 kWh (2024 on)",
        "usable_kwh": 65.4,
        "consumption": {"model": "quadratic", "a_wh_km": 57.0, "b_wh_km_per_kph2": 0.0123},
        "charge_curve": [
            [0, 44], [5, 100], [10, 105], [42, 105], [52, 80], [62, 61],
            [72, 47], [82, 36], [92, 25], [100, 8],
        ],
        "max_dc_kw": 105.0,
        "mass_kg": 1953.0,
        "top_speed_kph": 172.0,
        "source_note": "Hyundai Kona Electric, second generation (MY24-25): a 400 V car on its "
        "own platform, not E-GMP, and the slowest charger of the Koreans here at "
        "105 kW. Thirty-seven minutes for 10-80% is the number that decides its "
        "long trips, not the pack size. Curve fitted to EV Database's published "
        "figures.",
    },
    {
        "slug": "kia-ev6-74-rwd",
        "nameplate_slug": "kia-ev6",
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
        "slug": "kia-ev3-78",
        "nameplate_slug": "kia-ev3",
        "make": "Kia",
        "model": "EV3",
        "variant": "Long Range 78 kWh",
        "usable_kwh": 78.0,
        "consumption": {"model": "quadratic", "a_wh_km": 58.0, "b_wh_km_per_kph2": 0.0125},
        "charge_curve": [
            [0, 57], [5, 128], [10, 135], [43, 135], [53, 103], [63, 78],
            [73, 61], [83, 46], [93, 32], [100, 11],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2065.0,
        "top_speed_kph": 170.0,
        "source_note": "Kia EV3 (MY25-26): E-GMP underneath but a 400 V pack, so it charges "
        "nothing like the 800 V EV6 above it — 135 kW and 33 minutes for "
        "10-80%, against the EV6's 240 kW. Buyers routinely expect otherwise. "
        "Curve fitted to the 10-80% time published by EV Database.",
    },
    {
        "slug": "toyota-bz4x-71",
        "nameplate_slug": "toyota-bz4x",
        "make": "Toyota",
        "model": "bZ4X",
        "variant": "FWD 71 kWh (2026)",
        "usable_kwh": 71.0,
        "consumption": {"model": "quadratic", "a_wh_km": 59.0, "b_wh_km_per_kph2": 0.0132},
        "charge_curve": [
            [0, 63], [5, 142], [10, 150], [39, 150], [49, 114], [59, 87],
            [69, 68], [79, 51], [89, 36], [99, 24], [100, 12],
        ],
        "max_dc_kw": 150.0,
        "mass_kg": 2155.0,
        "top_speed_kph": 160.0,
        "source_note": "Toyota bZ4X FWD (MY26 facelift), and the model year matters more here "
        "than on any other car in this list. The 2022 car earned a reputation "
        "for collapsing DC power on a second consecutive stop; the facelift "
        "reworked the pack conditioning and reaches 10-80% in twenty-nine "
        "minutes. These numbers describe the 2026 car and should not be read "
        "as a defence of the original. Fitted to EV Database's figures.",
    },
    {
        "slug": "nissan-ariya-87",
        "nameplate_slug": "nissan-ariya",
        "make": "Nissan",
        "model": "Ariya",
        "variant": "87 kWh",
        "usable_kwh": 87.0,
        "consumption": {"model": "quadratic", "a_wh_km": 64.0, "b_wh_km_per_kph2": 0.0144},
        "charge_curve": [
            [0, 55], [5, 124], [10, 130], [33, 130], [43, 99], [53, 75],
            [63, 58], [73, 44], [83, 31], [93, 21], [100, 10],
        ],
        "max_dc_kw": 130.0,
        "mass_kg": 2301.0,
        "top_speed_kph": 160.0,
        "source_note": "Nissan Ariya 87 kWh (MY22-25): the slowest 10-80% in this catalog at "
        "forty-eight minutes — 87 kWh behind a 130 kW peak that starts tapering "
        "at a third full. Paired with the highest motorway consumption of the "
        "European-market cars here, it is the entry where cruise speed costs "
        "the most. Curve fitted to EV Database's published figures.",
    },
    {
        "slug": "byd-atto3-60",
        "nameplate_slug": "byd-atto-3",
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
        "nameplate_slug": "byd-dolphin",
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
        "nameplate_slug": "byd-seal",
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
        "nameplate_slug": "byd-seal-u",
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
        "nameplate_slug": "byd-sealion-7",
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
        "nameplate_slug": "mg-mg4",
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
        "nameplate_slug": "xpeng-g6",
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
        "nameplate_slug": "nio-et5",
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
        "nameplate_slug": "zeekr-x",
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
        "nameplate_slug": "leapmotor-c10",
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
        "nameplate_slug": "dongfeng-box",
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
        "nameplate_slug": "ford-mustang-mach-e",
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
        "nameplate_slug": "ford-f150-lightning",
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
        "nameplate_slug": "chevrolet-bolt-euv",
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
        "nameplate_slug": "chevrolet-equinox-ev",
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
        "nameplate_slug": "rivian-r1t",
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
        "nameplate_slug": "lucid-air",
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
        "nameplate_slug": "cadillac-lyriq",
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
