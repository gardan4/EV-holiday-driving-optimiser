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
