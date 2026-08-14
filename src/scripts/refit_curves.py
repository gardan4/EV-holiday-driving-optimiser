"""Propose catalog charge curves from a CC-BY dataset of measured DC profiles.

    uv run python -m scripts.refit_curves            # coverage + accuracy report
    uv run python -m scripts.refit_curves --json out.json

Source: Morin, Chatra, Whitacre & Michalek, "Digitized Electric Vehicle
Fast-Charging Profiles by Model (Power vs SOC/SOE, CSV Format)", figshare,
2025, doi:10.1184/R1/30570653, licensed **CC BY 4.0**. The profiles were
digitized from published Fastned and InsideEVs charging plots — the same
provenance `seed_vehicles.py` already claims for its curves, except measured
rather than drawn by hand. The licence is attribution-only, which is why every
car this script touches gets the citation in its `source_note`.

Nothing here runs in CI or at request time: it hits the network, and its output
is a PROPOSAL a human reads and pastes into `seed_vehicles.py`. The catalog
stays a hand-maintained file on purpose (see that module's docstring).

Why this exists: the shipped curves were fitted by eye and drifted up to 26%
from measured behaviour, in both directions, which is invisible without
something that recomputes charge times and compares them.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import urllib.request
import zipfile
from collections import defaultdict

from app.services.simulator import VehicleParams, charge_minutes
from scripts.seed_vehicles import VEHICLES

FIGSHARE_ARTICLE = 30570653
PROFILE_FILE = "main-data-fastcharge-profile-het.csv"
CITATION = (
    "Charge curve measured, not drawn: digitized from published Fastned and "
    "InsideEVs plots by Morin et al., figshare doi:10.1184/R1/30570653 (CC BY 4.0)."
)

CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")

# Windows the planner actually charges over; the fit is verified on all of them.
WINDOWS = ((10, 80), (10, 60), (20, 70), (15, 55), (30, 80), (50, 90))


# ---------------------------------------------------------------------------
# Source data
# ---------------------------------------------------------------------------


def _download() -> str:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, PROFILE_FILE)
    if os.path.exists(path):
        return open(path, encoding="utf-8-sig").read()
    meta = json.load(urllib.request.urlopen(
        f"https://api.figshare.com/v2/articles/{FIGSHARE_ARTICLE}", timeout=60))
    url = next(f["download_url"] for f in meta["files"] if f["name"] == PROFILE_FILE)
    raw = urllib.request.urlopen(url, timeout=300).read()
    if raw[:2] == b"PK":  # figshare sometimes serves a zip
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(PROFILE_FILE)
    text = raw.decode("utf-8-sig")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def profiles() -> dict[str, dict]:
    """short_name → {oem, model, usable_kwh, points [(soe%, kW)]}."""
    out: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(_download())):
        key = r["short_name"]
        try:
            soe, kw = float(r["soe.percent"]), float(r["power.kW"])
            usable = float(r["usable_pack.kWh"])
        except (ValueError, KeyError):
            continue
        p = out.setdefault(key, {"oem": r["oem_attributed"], "model": r["model"],
                                 "usable_kwh": usable, "points": []})
        p["points"].append((soe, kw))
    for p in out.values():
        p["points"].sort()
        p["peak_kw"] = max(k for _, k in p["points"])
        p["soc_lo"] = p["points"][0][0]
        p["soc_hi"] = p["points"][-1][0]
    return out


# ---------------------------------------------------------------------------
# Curve construction
# ---------------------------------------------------------------------------


def resample(points: list[tuple[float, float]], step: int = 1) -> dict[int, float]:
    """Mean power in each `step`-wide SoC bucket. The profiles carry ~180
    unevenly spaced samples; bucketing gives an even grid and averages away the
    jitter that plot digitization introduces."""
    buckets: dict[int, list[float]] = defaultdict(list)
    for soe, kw in points:
        buckets[int(soe // step) * step].append(kw)
    return {s: sum(v) / len(v) for s, v in sorted(buckets.items())}


def extend_to_full_range(grid: dict[int, float], spec_peak_kw: float | None = None) -> dict[int, float]:
    """Cover 0-100%, which the catalog requires and the profiles do not.

    Sessions start where the driver plugged in (median 6%) and stop where they
    unplugged (median 94%), so both ends are extrapolated.

    The low end is anchored to the car's SPEC peak rather than to the first
    measured sample. A measured maximum is a property of one session: the Model
    Y profile begins at 8.2% and peaks on its very first point, so its 230 kW is
    simply where that driver plugged in, not what the car does — and adopting it
    would quietly restate a 250 kW car as a 230 kW one on the /ev pages. Where
    the session did capture the peak mid-curve, it agrees with the catalog to
    within a few percent (Ioniq 5 221 vs 220, EV6 241 vs 240, i4 208 vs 205),
    which is what justifies trusting the spec figure for the part nobody
    measured. The invented region sits below the planner's default reserve SoC,
    so it barely reaches a real plan.

    The top is tapered linearly to a trickle: no session data, and inventing
    power up there would have the planner recommend a stop it cannot deliver.
    """
    g = dict(grid)
    lo, hi = min(g), max(g)
    start_kw = g[lo]
    if spec_peak_kw and spec_peak_kw > start_kw and lo > 0:
        for s in range(0, lo):
            frac = s / lo
            g[s] = spec_peak_kw + (start_kw - spec_peak_kw) * frac
    else:
        for s in range(0, lo):
            g[s] = start_kw
    if hi < 100:
        end = g[hi]
        for s in range(hi + 1, 101):
            frac = (s - hi) / (100 - hi)
            g[s] = max(end * (1 - frac) + 8.0 * frac, 8.0)
    return g


def _interp(curve, soc: float) -> float:
    if soc <= curve[0][0]:
        return curve[0][1]
    for (s0, p0), (s1, p1) in zip(curve, curve[1:]):
        if soc <= s1:
            return p1 if s1 == s0 else p0 + (soc - s0) / (s1 - s0) * (p1 - p0)
    return curve[-1][1]


def _params(curve, kwh: float) -> VehicleParams:
    return VehicleParams(usable_kwh=kwh, a_wh_km=150.0, b_wh_km_per_kph2=0.01,
                         charge_curve=tuple((float(s), float(k)) for s, k in curve),
                         charge_efficiency=1.0)


def _dp(points: list[tuple[int, float]], eps: float) -> list[tuple[int, float]]:
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = (dx * dx + dy * dy) ** 0.5 or 1.0
    wi, wd = 0, -1.0
    for i in range(1, len(points) - 1):
        x, y = points[i]
        d = abs(dy * x - dx * y + x1 * y0 - y1 * x0) / norm
        if d > wd:
            wi, wd = i, d
    if wd <= eps:
        return [points[0], points[-1]]
    return _dp(points[: wi + 1], eps)[:-1] + _dp(points[wi:], eps)


def fit(grid: dict[int, float], kwh: float, tol: float = 0.01,
        max_points: int = 18) -> tuple[list, dict]:
    """Fewest anchor points whose charge times match the full grid within `tol`."""
    full = sorted(grid.items())
    peak = max(k for _, k in full)
    peak_soc = min(s for s, k in full if k == peak)
    best = None
    eps = max(peak * 0.12, 1.0)
    for _ in range(60):
        socs = {s for s, _ in _dp(full, eps)} | {0, 100, peak_soc}
        curve = [(s, grid[s]) for s in sorted(socs) if s in grid]
        errs = {w: 0.0 for w in WINDOWS}
        site = peak * 10
        a, b = _params(curve, kwh), _params(full, kwh)
        for lo, hi in WINDOWS:
            tb = charge_minutes(b, lo, hi, site)
            errs[(lo, hi)] = (charge_minutes(a, lo, hi, site) / tb - 1) if tb else 0.0
        worst = max(abs(v) for v in errs.values())
        if len(curve) <= max_points:
            cand = (curve, {"n": len(curve), "worst": worst,
                            "max_kw_err": max(abs(_interp(curve, s) - k) for s, k in full)})
            # Rank: clearing tolerance beats not clearing it; among those that
            # clear, fewest anchors wins; among those that don't, most accurate
            # wins. Keeping the first candidate seen instead means the COARSEST
            # fit survives for every car that never clears tolerance, which is
            # how an Ioniq 5 ended up modelled by four points and 9% fast.
            def rank(c):
                d = c[1]
                return (0 if d["worst"] <= tol else 1,
                        d["n"] if d["worst"] <= tol else d["worst"])
            if best is None or rank(cand) < rank(best):
                best = cand
        eps *= 0.82
    return best


def round_curve(curve) -> list[list[float]]:
    out, seen = [], set()
    for s, k in curve:
        s = int(round(s))
        if s in seen:
            continue
        seen.add(s)
        out.append([s, round(float(k), 1)])
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

# Our slug → the dataset's short_name. Matching is by hand and then CHECKED
# against usable kWh and peak kW below, because a near-name match across model
# years is exactly how a curve for the wrong car gets shipped.
MATCHES: dict[str, str] = {
    "tesla-modely-lr": "Tesla_Model Y LR_75",
    "tesla-model3-lr": "Tesla_Model 3 LR_75",
    "tesla-model3-rwd": "Tesla_Model 3 SR 2023_57.5",
    "tesla-models-lr": "Tesla_Model S_supercharger_98.4",
    "tesla-models-plaid": "Tesla_Model S Plaid_98.4",
    "tesla-modelx-lr": "Tesla_Model X_supercharger_95",
    "tesla-modelx-plaid": "Tesla_Model X Plaid_98.4",
    "hyundai-ioniq5-73": "Hyundai_IONIQ 5 Long Range (77.4 kWh)_70",
    "hyundai-ioniq5-84": "Hyundai_IONIQ 5 Long Range (2024 update)_80",
    "hyundai-ioniq6-74-rwd": "Hyundai_IONIQ 6 Long Range (77.4 kWh)_74",
    "hyundai-kona-65": "Hyundai_Kona Electric 65 (2024 new)_65.4",
    "hyundai-kona-64": "Hyundai_Kona Electric 64 (2nd gen)_64",
    "kia-ev6-74-rwd": "Kia_EV6_74",
    "kia-ev3-78": "Kia_EV3 Long Range_78",
    # Deliberately NO Skoda Enyaq at all. Every Enyaq-77 profile in the dataset
    # peaks near 179 kW, and Skoda's own spec says that is the 85x (AWD) and RS:
    # the 85 RWD we model is a 135 kW car, and the 2021-2024 iV 80 is 125-135.
    # The dataset's naming ("Enyaq iV 80 _ 85", "Enyaq 85") distinguishes
    # neither the drivetrain nor the generation, so any of them would model a
    # different car at a third more power.
    #
    # Same reason there is no vw-id3-pro-s-79: the ID.3 profile peaks at 100 kW
    # against that car's 185 kW spec, so it is not the variant we ship.
    "bmw-i4-edrive40": "BMW_i4_80.9",
    "bmw-i5-edrive40": "BMW_i5_81.2",
    "bmw-ix1-edrive20": "BMW_iX1_64.7",
    "ford-mustang-mach-e-er": "Ford_Mustang Mach-E Extended (2024)_91",
    "audi-q4-45": "Audi_Q4 e-tron 40/45 (RWD/AWD)_77",
    "renault-megane-60": "Renault_Megane E-Tech V60_60",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", help="write proposed curves here")
    args = ap.parse_args()

    prof = profiles()
    cat = {v["slug"]: v for v in VEHICLES}
    proposals, problems = {}, []

    print(f"{len(prof)} profiles in the dataset, {len(cat)} cars in the catalog, "
          f"{len(MATCHES)} matched\n")
    print(f"{'slug':<26}{'kWh o/d':>10}{'peak o/d':>11}{'10-80 now':>11}"
          f"{'10-80 new':>11}{'pts':>5}{'fit':>8}")
    print("-" * 82)

    for slug, key in MATCHES.items():
        v, p = cat.get(slug), prof.get(key)
        if not v or not p:
            problems.append((slug, "no such slug" if not v else f"no profile {key!r}"))
            continue
        grid = extend_to_full_range(resample(p["points"]), v["max_dc_kw"])
        curve, diag = fit(grid, v["usable_kwh"])
        rounded = round_curve(curve)
        site = max(p["peak_kw"], v["max_dc_kw"]) * 10
        now = charge_minutes(_params(v["charge_curve"], v["usable_kwh"]), 10, 80, site)
        new = charge_minutes(_params(rounded, v["usable_kwh"]), 10, 80, site)

        # Sanity gates. A curve that disagrees with the catalog on pack size or
        # peak power is a match error, not a correction, and must not be applied.
        if abs(v["usable_kwh"] / p["usable_kwh"] - 1) > 0.08:
            problems.append((slug, f"capacity mismatch: ours {v['usable_kwh']} vs "
                                   f"dataset {p['usable_kwh']}"))
            continue
        peak_soc = min(s for s, k in p["points"] if k == p["peak_kw"])
        captured = peak_soc - p["soc_lo"] >= 2.0
        if captured and abs(v["max_dc_kw"] / p["peak_kw"] - 1) > 0.25:
            problems.append((slug, f"peak mismatch: ours {v['max_dc_kw']} vs measured "
                                   f"{p['peak_kw']:.0f} (peak captured at {peak_soc:.0f}% "
                                   f"— likely wrong variant or a thermally limited session)"))
            continue

        # `max_dc_kw` must equal the curve peak (test_vehicle_catalog), so a
        # curve peaking a hair off the spec figure would silently restate the
        # car's advertised power. Within 3% that gap is digitization noise:
        # clip to the spec value and leave the headline number alone. Beyond
        # that it is a real disagreement — our number, the match, or the model
        # year — and a human decides, because "the Enyaq now charges at 179 kW"
        # is a product claim, not a curve tweak.
        curve_peak = max(k for _, k in rounded)
        if abs(curve_peak / v["max_dc_kw"] - 1) <= 0.03:
            rounded = [[s, min(k, v["max_dc_kw"])] for s, k in rounded]
            for pt in rounded:
                if pt[1] == max(k for _, k in rounded):
                    pt[1] = v["max_dc_kw"]
                    break
        else:
            problems.append((slug, f"curve peaks at {curve_peak:.0f} kW but catalog says "
                                   f"{v['max_dc_kw']:.0f} ({curve_peak / v['max_dc_kw'] - 1:+.0%}) "
                                   f"— changes an advertised spec, decide by hand"))
            continue

        proposals[slug] = {"charge_curve": rounded, "max_dc_kw": max(k for _, k in rounded),
                           "dataset_usable_kwh": p["usable_kwh"],
                           "measured_span": [p["soc_lo"], p["soc_hi"]],
                           "citation": CITATION, "fit": diag}
        print(f"{slug:<26}{v['usable_kwh']:5.0f}/{p['usable_kwh']:<4.0f}"
              f"{v['max_dc_kw']:6.0f}/{p['peak_kw']:<4.0f}{now:10.1f}m{new:10.1f}m"
              f"{diag['n']:5d}{diag['worst']:+8.1%}")

    if problems:
        print("\nNOT applied — needs a human:")
        for slug, why in problems:
            print(f"  {slug:<26} {why}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(proposals, f, indent=1)
        print(f"\nwrote {len(proposals)} proposals to {args.json}")


if __name__ == "__main__":
    main()
