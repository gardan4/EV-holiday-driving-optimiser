"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
import {
  Units,
  consumptionUnit,
  distUnit,
  fmtClimb,
  fmtConsumption,
  fmtDist,
  fmtRangeKm,
  fmtSpeed,
  distIn,
  perDistance,
  speedIn,
  speedToKph,
  speedUnit,
  unitsForLocale,
} from "./units"

/**
 * The reader's choice of km/h or mph, remembered in this browser.
 *
 * Metric is what the server renders, always — the preference lives in
 * localStorage, which the server cannot see, so a first render that read it
 * would hydrate into a mismatch. It is picked up in an effect instead: an
 * imperial reader sees one metric paint and then their own units, which is the
 * same deal `TripsButton` makes for the username.
 *
 * The formatters are re-exported bound to the current choice, so a call site
 * writes `speed(kph)` rather than threading `units` into every helper — and
 * cannot format half a screen in the wrong one.
 */

const UNITS_KEY = "evtrip.units"

interface UnitsState {
  units: Units
  setUnits: (u: Units) => void
  /** False until the stored preference has been read. Controls that would
   *  otherwise flash the wrong state can wait on it. */
  mounted: boolean
  /** 130 → "130 km/h" / "81 mph". */
  speed: (kph: number) => string
  /** 130 → 130 / 81. The bare number, for a chart tick or an input. */
  speedValue: (kph: number) => number
  /** What the reader typed → km/h for the API. */
  toKph: (value: number) => number
  /** Metres → "912 km" / "567 mi". */
  dist: (meters: number) => string
  /** Metres → the bare number. */
  distValue: (meters: number) => number
  /** Kilometres → "45 km" / "28 mi". */
  rangeKm: (km: number) => string
  /** Wh/km → "182 Wh/km" / "293 Wh/mi". */
  consumption: (whPerKm: number) => string
  /** Metres of ascent → "1.2 km" / "3,900 ft". */
  climb: (meters: number) => string
  /** "km/h" / "mph". */
  speedUnit: string
  /** "km" / "mi". */
  distUnit: string
  /** "Wh/km" / "Wh/mi". */
  consumptionUnit: string
  /** "per km" / "per mile". */
  perDistance: string
}

const UnitsContext = createContext<UnitsState | null>(null)

export function UnitsProvider({ children }: { children: React.ReactNode }) {
  const [units, setUnitsState] = useState<Units>("metric")
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    try {
      const stored = window.localStorage.getItem(UNITS_KEY)
      if (stored === "metric" || stored === "imperial") {
        setUnitsState(stored)
        return
      }
    } catch {
      // Private mode, or storage blocked. Fall through to the locale guess.
    }
    // Nobody has chosen: start where this browser's region drives.
    setUnitsState(unitsForLocale(navigator.language))
  }, [])

  const setUnits = useCallback((next: Units) => {
    setUnitsState(next)
    try {
      window.localStorage.setItem(UNITS_KEY, next)
    } catch {
      // The choice still applies to this page; it just won't be remembered.
    }
  }, [])

  const value = useMemo<UnitsState>(
    () => ({
      units,
      setUnits,
      mounted,
      speed: (kph) => fmtSpeed(kph, units),
      speedValue: (kph) => speedIn(kph, units),
      toKph: (v) => speedToKph(v, units),
      dist: (m) => fmtDist(m, units),
      distValue: (m) => distIn(m, units),
      rangeKm: (km) => fmtRangeKm(km, units),
      consumption: (wh) => fmtConsumption(wh, units),
      climb: (m) => fmtClimb(m, units),
      speedUnit: speedUnit(units),
      distUnit: distUnit(units),
      consumptionUnit: consumptionUnit(units),
      perDistance: perDistance(units),
    }),
    [units, setUnits, mounted]
  )

  return <UnitsContext.Provider value={value}>{children}</UnitsContext.Provider>
}

/**
 * Units for a client component.
 *
 * Falls back to metric outside the provider rather than throwing: these
 * formatters run in leaf components that a test or a story can render on their
 * own, and a units helper is not worth a crash.
 */
export function useUnits(): UnitsState {
  const ctx = useContext(UnitsContext)
  return ctx ?? FALLBACK
}

const FALLBACK: UnitsState = {
  units: "metric",
  setUnits: () => {},
  mounted: false,
  speed: (kph) => fmtSpeed(kph, "metric"),
  speedValue: (kph) => speedIn(kph, "metric"),
  toKph: (v) => speedToKph(v, "metric"),
  dist: (m) => fmtDist(m, "metric"),
  distValue: (m) => distIn(m, "metric"),
  rangeKm: (km) => fmtRangeKm(km, "metric"),
  consumption: (wh) => fmtConsumption(wh, "metric"),
  climb: (m) => fmtClimb(m, "metric"),
  speedUnit: speedUnit("metric"),
  distUnit: distUnit("metric"),
  consumptionUnit: consumptionUnit("metric"),
  perDistance: perDistance("metric"),
}
