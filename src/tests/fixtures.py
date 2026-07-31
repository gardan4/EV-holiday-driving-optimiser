"""Shared synthetic fixtures for simulator tests and the demo CLI. No network."""

from __future__ import annotations

from app.services.simulator import ChargerNode, RouteSegment, VehicleParams

# Cupra Born 58 kWh — mirrors scripts/seed_vehicles.py.
BORN_58 = VehicleParams(
    usable_kwh=58.0,
    a_wh_km=55.0,
    b_wh_km_per_kph2=0.0105,
    charge_curve=(
        (0, 50), (5, 118), (10, 120), (30, 120), (35, 105), (45, 85),
        (55, 68), (65, 55), (75, 42), (85, 30), (95, 18), (100, 10),
    ),
)


def flat_motorway(total_km: float, freeflow: float = 120.0, country: str = "DE",
                  seg_km: float = 10.0) -> list[RouteSegment]:
    """A uniform motorway route chopped into segments."""
    n = int(total_km / seg_km)
    return [RouteSegment(dist_m=seg_km * 1000.0, freeflow_kph=freeflow, country=country)
            for _ in range(n)]


def chargers_every(total_km: float, spacing_km: float, power_kw: float = 150.0,
                   start_km: float | None = None) -> list[ChargerNode]:
    if start_km is None:
        start_km = spacing_km
    out = []
    km = start_km
    idx = 0
    while km < total_km - 5:
        out.append(ChargerNode(
            charger_id=f"chg-{idx}",
            name=f"Charger {idx} ({power_kw:.0f} kW)",
            offset_m=km * 1000.0,
            power_kw=power_kw,
            detour_min=2.0,
        ))
        km += spacing_km
        idx += 1
    return out


def nl_to_austria_route() -> tuple[list[RouteSegment], list[ChargerNode]]:
    """Synthetic Utrecht → Innsbruck-ish corridor, ~950 km.

    ~80 km NL motorway (cap 130), ~600 km DE motorway (mostly derestricted with
    a few 100-limited roadwork/urban stretches), ~270 km AT motorway (cap 130),
    plus slow urban caps at both ends. Ionity-style 300 kW sites every ~90 km
    with 150 kW sites in between.
    """
    segs: list[RouteSegment] = []
    segs.append(RouteSegment(5_000, 50.0, "NL"))            # city exit
    segs += flat_motorway(80, freeflow=115, country="NL")
    # German leg (~610 km): realistically only ~⅓ derestricted; the rest is
    # signposted 120/130 zones, roadworks, and slow limited stretches.
    segs += flat_motorway(100, freeflow=125, country="DE")  # derestricted
    segs += flat_motorway(80, freeflow=110, country="DE")   # 120 zone
    segs += flat_motorway(60, freeflow=95, country="DE")    # roadworks-ish
    segs += flat_motorway(150, freeflow=125, country="DE")  # derestricted
    segs += flat_motorway(30, freeflow=80, country="DE")    # limited stretch
    segs += flat_motorway(120, freeflow=110, country="DE")  # 120/130 zones
    segs += flat_motorway(70, freeflow=125, country="DE")   # derestricted
    segs += flat_motorway(230, freeflow=115, country="AT")
    segs.append(RouteSegment(8_000, 45.0, "AT"))            # valley approach

    total_km = sum(s.dist_m for s in segs) / 1000.0
    fast = chargers_every(total_km, 90.0, power_kw=300.0, start_km=60.0)
    mid = chargers_every(total_km, 90.0, power_kw=150.0, start_km=105.0)
    chargers = sorted(fast + mid, key=lambda c: c.offset_m)
    # Re-id for stable naming after the merge.
    chargers = [
        ChargerNode(
            charger_id=f"chg-{i}",
            name=f"{'Ionity' if c.power_kw >= 300 else 'Fastned'} km{int(c.offset_m / 1000)}",
            offset_m=c.offset_m,
            power_kw=c.power_kw,
            detour_min=c.detour_min,
        )
        for i, c in enumerate(chargers)
    ]
    return segs, chargers
