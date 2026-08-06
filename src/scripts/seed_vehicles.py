"""Idempotent vehicle-catalog seeder (upsert by slug).

Run manually with `uv run python -m scripts.seed_vehicles`, and invoked from
db_bootstrap when the vehicles table is empty.

Data provenance: consumption is a fitted quadratic Wh/km(v) = a + b·v² and the
DC charge curve is piecewise-linear (soc% → kW, cable-side), both hand-curated
to match public fast-charge tests and range tests (Fastned charging guides, P3
Charging Index, InsideEVs/Bjørn Nyland 1000 km tests). They are deliberately
conservative approximations — good enough to compare cruise speeds, not lab data.

`mass_kg` is kerb mass plus a nominal 180 kg of occupants and luggage; it only
feeds the elevation term. `top_speed_kph` is the electronically limited top
speed — the sweep stops there, because simulating a Born at 175 is fiction.
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
        "source_note": _MEB_NOTE,
        "sort_order": 10,
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
        "source_note": _MEB_NOTE,
        "sort_order": 11,
    },
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
        "source_note": _MEB_NOTE,
        "sort_order": 20,
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
        "sort_order": 21,
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
        "sort_order": 22,
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
        "sort_order": 30,
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
        "sort_order": 31,
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
        "sort_order": 32,
    },
    {
        "slug": "skoda-enyaq-77",
        "make": "Škoda",
        "model": "Enyaq",
        "variant": "80 (77 kWh)",
        "usable_kwh": 77.0,
        "consumption": {"model": "quadratic", "a_wh_km": 60.0, "b_wh_km_per_kph2": 0.0122},
        "charge_curve": [
            [0, 55], [5, 130], [10, 135], [30, 135], [40, 110], [50, 90],
            [60, 75], [70, 60], [80, 45], [90, 28], [100, 12],
        ],
        "max_dc_kw": 135.0,
        "mass_kg": 2290.0,
        "top_speed_kph": 180.0,
        "source_note": _MEB_NOTE,
        "sort_order": 40,
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
        "sort_order": 50,
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
        "sort_order": 51,
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
        "sort_order": 55,
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
        "sort_order": 60,
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
        "sort_order": 61,
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
        "sort_order": 70,
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
        "sort_order": 71,
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
        "sort_order": 72,
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
        "sort_order": 80,
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
        "sort_order": 90,
    },
    # ── Chinese brands ───────────────────────────────────────────────────────
    # Worth having precisely because they break the "bigger battery, faster
    # trip" intuition: several are cheap, heavy and slow-charging, which is the
    # case where cruise speed matters most. See _CHINA_CAVEAT on the data.
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
        "sort_order": 100,
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
        "sort_order": 101,
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
        "sort_order": 102,
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
        "sort_order": 103,
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
        "sort_order": 104,
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
        "sort_order": 110,
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
        "sort_order": 111,
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
        "sort_order": 112,
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
        "sort_order": 113,
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
        "sort_order": 114,
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
        "sort_order": 115,
    },
]


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
