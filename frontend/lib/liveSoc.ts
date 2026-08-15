/**
 * The battery between server updates.
 *
 * The server owns the battery model — `live.current_soc` while driving,
 * `live.charging_soc` while plugged in — and it is the only thing that ever
 * anchors it. But its answer only reaches the phone when a request comes back,
 * which is every ~25 s on a ping and every ~10 s on a poll. In between, the
 * position moves smoothly (the GPS watch projects it locally) and the battery
 * does not, so a driver watching the screen sees the kilometres tick down
 * against a percentage that jumps a whole point at a time and reads as stale —
 * reported from the road as the number "lagging behind".
 *
 * So the same two models are ported here, run only over the gap since the last
 * server answer, and thrown away the instant a fresh one lands. The gap is
 * measured on the SERVER's clock — `state.at_min` is minutes since departure
 * at the moment it answered, and `started_at` says when that was — rather than
 * on when the reply happened to reach the phone. That needs no state to track,
 * survives a re-render, and cannot be thrown out by a slow connection.
 *
 * Three rules make it safe:
 *
 * - **It only ever extends the server's own answer.** Every projection starts
 *   from `state.soc` (or, at a plug, from `charging.start_soc` and the server's
 *   own `since_min`), so the client cannot invent a different battery — it can
 *   only carry the server's forward by a few seconds.
 * - **It uses the same maths.** `consumptionWhKm` and the charge curve in
 *   `lib/vehicles.ts` are the deliberate port of the simulator's; this adds no
 *   third opinion.
 * - **It is capped.** Past `MAX_GAP_S` the phone has stopped hearing from the
 *   server and extrapolating further would be guessing, not smoothing, so it
 *   holds the last real figure — the same instinct as the "stale" banner.
 *
 * It does NOT touch `run_factor`, calibration, or anything persisted. Nothing
 * here is ever sent anywhere.
 */

import { LiveState, Vehicle } from "@/lib/client"
import { parseServerTime } from "@/lib/format"
import { consumptionWhKm, curvePowerKw } from "@/lib/vehicles"

/** Beyond this long without a server answer, hold rather than extrapolate. */
const MAX_GAP_S = 180

/** Below this the gap is round-trip noise, not elapsed time. Smoothing it
 *  would make the number jitter rather than move. */
const MIN_GAP_S = 1

/** Integration step for marching the charge curve forward, in SoC points.
 *  The same 0.5 the simulator and `chargeMinutes` use. */
const STEP = 0.5

/** SoC reached after `minutes` at a site limited to `siteKw` — the inverse of
 *  `chargeMinutes`, marched on time. Mirrors `simulator.charge_forward`,
 *  including the cable→battery derating applied AFTER the site cap. */
export function chargeForward(
  v: Vehicle,
  soc: number,
  minutes: number,
  siteKw: number
): number {
  if (minutes <= 0 || siteKw <= 0) return soc
  const eff = Math.max(v.charge_efficiency ?? 0.92, 0.5)
  let s = soc
  let left = minutes
  while (s < 100 - 1e-9 && left > 1e-9) {
    const step = Math.min(STEP, 100 - s)
    const kw = Math.max(Math.min(curvePowerKw(v, s + step / 2), siteKw) * eff, 1)
    const m = (60 * ((v.usable_kwh * step) / 100)) / kw
    if (m >= left) return s + step * (left / m)
    s += step
    left -= m
  }
  return Math.min(s, 100)
}

/**
 * What to show right now, given the last thing the server said.
 *
 * @param state       the last server answer
 * @param startedAt   when the drive began, ISO-8601 from the server, so the
 *                    gap is measured on the same clock `state.at_min` counts
 * @param vehicle     the car, for the consumption fit and the charge curve
 * @param distM       where the car is now — the locally projected offset, which
 *                    is what moves between pings
 * @param speedKph    the cruise the plan is holding, used to price the gap
 * @param now         `Date.now()`, passed in so the caller owns the tick
 */
export function projectedSoc({
  state,
  startedAt,
  vehicle,
  distM,
  speedKph,
  now,
}: {
  state: LiveState
  startedAt: string
  vehicle: Vehicle
  distM: number
  speedKph: number
  now: number
}): number {
  const began = parseServerTime(startedAt)
  if (!Number.isFinite(began)) return state.soc
  const gapS = (now - began) / 1000 - state.at_min * 60
  // Off-route the server itself refuses to guess; so does this.
  if (state.stale || gapS < MIN_GAP_S || gapS > MAX_GAP_S) return state.soc

  // Plugged in: the clock is the whole model, and the server has already told
  // us where the session started and how far into it its own answer was.
  const ch = state.charging
  if (ch) {
    const minutes = ch.since_min + gapS / 60
    return clamp(chargeForward(vehicle, ch.start_soc, minutes, ch.power_kw))
  }

  // Driving: bill the metres covered since the server's answer, at the plan's
  // cruise. Only ever forward — a wobbly fix must not walk the battery back up,
  // which is the same rule `live.advance` follows on the offset.
  const dM = distM - state.offset_m
  if (dM <= 0) return state.soc
  const kwh = (consumptionWhKm(vehicle, speedKph) * (dM / 1000)) / 1000
  return clamp(state.soc - (kwh / Math.max(vehicle.usable_kwh, 1e-6)) * 100)
}

const clamp = (n: number) => Math.max(0, Math.min(100, n))
