"""CLI demo: the Born NL→Austria answer table on the synthetic fixture route.

    uv run python -m scripts.demo_sim
"""

from __future__ import annotations

from app.services.simulator import SimParams, optimum, sweep
from tests.fixtures import BORN_58, nl_to_austria_route


def fmt_hm(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h{m:02d}"


def main() -> None:
    segs, chargers = nl_to_austria_route()
    total_km = sum(s.dist_m for s in segs) / 1000.0
    speeds = [float(v) for v in range(90, 165, 5)]
    p = SimParams(depart_soc=100.0, target_soc=10.0)

    results = sweep(segs, chargers, BORN_58, speeds, p)
    best = optimum(results)

    print(f"Cupra Born 58 kWh · synthetic NL→AT corridor · {total_km:.0f} km")
    print(f"depart 100% · arrive ≥{p.target_soc:.0f}% · reserve {p.reserve_soc:.0f}%\n")
    print(f"{'kph':>5} {'total':>7} {'drive':>7} {'charge':>7} {'stops':>5}   vs best")
    for r in results:
        if not r.feasible:
            print(f"{r.speed_kph:>5.0f} {'—':>7}  (infeasible: charger gaps too large)")
            continue
        delta = r.total_min - best.total_min
        marker = "  ← optimum" if r is best else f"  +{delta:.0f} min" if delta else ""
        print(
            f"{r.speed_kph:>5.0f} {fmt_hm(r.total_min):>7} {fmt_hm(r.drive_min):>7} "
            f"{r.charge_min:>6.0f}m {r.n_stops:>5}{marker}"
        )

    print(f"\nOptimal plan at {best.speed_kph:.0f} kph:")
    for s in best.stops:
        print(
            f"  km {s.offset_m / 1000:>4.0f}  {s.name:<18} arrive {s.arrive_soc:>4.1f}% "
            f"→ charge {s.charge_min:>4.1f} min → {s.depart_soc:.0f}%"
        )


if __name__ == "__main__":
    main()
