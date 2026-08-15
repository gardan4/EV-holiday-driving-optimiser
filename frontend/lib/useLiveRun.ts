"use client"

/**
 * Following a drive from the browser.
 *
 * Two roles share this hook. The DRIVER's device holds a run id in
 * localStorage, watches GPS, and posts; anyone else opening the same link has
 * no token and simply polls the read-only view. That split is the security
 * model — see `src/app/api/runs.py`.
 *
 * Pacing is set by the server's limits: 12 pings/minute per run, so a fix goes
 * up every 25 seconds, and watchers poll every 10.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
  LiveRun,
  LiveState,
  RevisedPlan,
  finishRun,
  getLiveRun,
  pingRun,
  recordSoc,
  replanRun,
} from "./client"
import { RouteIndex, project } from "./route"

const PING_EVERY_MS = 25_000
const POLL_EVERY_MS = 10_000
/** Below this, the car is standing still — its time is billed to the heater,
 *  not to aerodynamic drag. */
const MOVING_KPH = 4

export function tokenKey(tripId: string) {
  return `evtrip:run:${tripId}`
}

/** Where "I stopped sharing my location" is remembered. It used to be plain
 *  component state, so a tab reload, a back-navigation, or iOS evicting the tab
 *  silently resumed broadcasting — a withdrawal of consent that undoes itself
 *  is not a withdrawal. Keyed per trip, next to the driver token. */
export function sharingKey(tripId: string) {
  return `evtrip:sharing:${tripId}`
}

function readSharing(tripId: string): boolean {
  if (typeof window === "undefined") return true
  try {
    return window.localStorage.getItem(sharingKey(tripId)) !== "off"
  } catch {
    return true
  }
}

export function readToken(tripId: string): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(tokenKey(tripId))
  } catch {
    return null
  }
}

export function storeToken(tripId: string, runId: string) {
  try {
    window.localStorage.setItem(tokenKey(tripId), runId)
  } catch {
    /* private browsing — the drive still works, it just won't survive a reload */
  }
}

export function clearToken(tripId: string) {
  try {
    window.localStorage.removeItem(tokenKey(tripId))
  } catch {
    /* ignore */
  }
}

export interface LiveHandle {
  /** Server truth. Null until the first response. */
  run: LiveRun | null
  state: LiveState | null
  /** Position snapped locally, so the map moves between pings. */
  localOffsetM: number | null
  isDriver: boolean
  gpsError: string | null
  busy: boolean
  /** Whether this device is currently posting its location. */
  sharing: boolean
  /** Pause/resume telemetry WITHOUT ending the drive — otherwise the only way
   *  to stop broadcasting your position is to declare you've arrived. */
  toggleSharing: () => void
  /** Returns the state the reading produced, so the caller can react to what
   *  the correction revealed rather than to what it was before. */
  submitSoc: (soc: number) => Promise<LiveState | null>
  requestReplan: () => Promise<RevisedPlan | null>
  end: () => Promise<void>
}

export function useLiveRun(
  tripId: string,
  routeIndex: RouteIndex | null,
  initial: LiveRun | null
): LiveHandle {
  const [run, setRun] = useState<LiveRun | null>(initial)
  const [state, setState] = useState<LiveState | null>(initial?.state ?? null)
  const [localOffsetM, setLocalOffsetM] = useState<number | null>(null)
  const [gpsError, setGpsError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [runId, setRunId] = useState<string | null>(null)
  const [sharing, setSharing] = useState(true)
  // Read on mount rather than as the initial value: localStorage is not
  // available during the server render, and disagreeing with it would hydrate
  // the wrong toggle state.
  useEffect(() => setSharing(readSharing(tripId)), [tripId])

  // Kinematics accumulated between posts. Kept in a ref: these update on every
  // GPS fix and must not re-render anything.
  const acc = useRef({
    movingS: 0,
    stationaryS: 0,
    lastFixAt: 0,
    lat: 0,
    lon: 0,
    accuracy: undefined as number | undefined,
    have: false,
  })
  const finished = run?.status === "finished"

  useEffect(() => {
    setRunId(readToken(tripId))
  }, [tripId])

  const isDriver = runId !== null && !finished
  const broadcasting = isDriver && sharing

  // --- watcher: poll the read-only view -----------------------------------
  useEffect(() => {
    if (isDriver || finished) return
    let alive = true
    const tick = async () => {
      try {
        const r = await getLiveRun(tripId)
        if (!alive) return
        setRun(r)
        setState(r.state)
      } catch {
        /* the drive may simply not have started yet */
      }
    }
    void tick()
    const h = setInterval(tick, POLL_EVERY_MS)
    return () => {
      alive = false
      clearInterval(h)
    }
  }, [tripId, isDriver, finished])

  // --- driver: watch GPS ---------------------------------------------------
  useEffect(() => {
    if (!broadcasting || !routeIndex) return
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setGpsError("This browser can't share its location.")
      return
    }

    const id = navigator.geolocation.watchPosition(
      (pos) => {
        setGpsError(null)
        const now = Date.now()
        const a = acc.current
        const dt = a.have ? (now - a.lastFixAt) / 1000 : 0
        // Prefer the device's own speed; fall back to distance over time.
        let kph = pos.coords.speed != null ? pos.coords.speed * 3.6 : 0
        if (pos.coords.speed == null && a.have && dt > 0) {
          const p = project(routeIndex, pos.coords.latitude, pos.coords.longitude)
          const prev = project(routeIndex, a.lat, a.lon)
          kph = (Math.abs(p.offsetM - prev.offsetM) / 1000) * (3600 / dt)
        }
        if (dt > 0 && dt < 300) {
          if (kph >= MOVING_KPH) a.movingS += dt
          else a.stationaryS += dt
        }
        a.lastFixAt = now
        a.lat = pos.coords.latitude
        a.lon = pos.coords.longitude
        a.accuracy = pos.coords.accuracy
        a.have = true

        const proj = project(routeIndex, pos.coords.latitude, pos.coords.longitude)
        setLocalOffsetM(proj.offRouteM > 500 ? null : proj.offsetM)
      },
      (err) => {
        setGpsError(
          err.code === err.PERMISSION_DENIED
            ? "Location is blocked. Allow it to follow the drive automatically."
            : "Can't get a location fix right now."
        )
      },
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 20_000 }
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [broadcasting, routeIndex])

  // --- driver: post a fix on a fixed cadence -------------------------------
  useEffect(() => {
    if (!broadcasting || !runId) return
    let alive = true
    const send = async () => {
      const a = acc.current
      if (!a.have) return
      const movingS = a.movingS
      const stationaryS = a.stationaryS
      a.movingS = 0
      a.stationaryS = 0
      try {
        const st = await pingRun(runId, {
          lat: a.lat,
          lon: a.lon,
          moving_s: Math.min(movingS, 3600),
          stationary_s: Math.min(stationaryS, 3600),
          accuracy_m: a.accuracy,
        })
        if (alive) setState(st)
      } catch {
        // Put the time back so a dropped ping doesn't lose the drag it stood
        // for — the next one bills the whole window.
        a.movingS += movingS
        a.stationaryS += stationaryS
      }
    }
    const h = setInterval(send, PING_EVERY_MS)
    return () => {
      alive = false
      clearInterval(h)
    }
  }, [broadcasting, runId])

  // Keep the screen awake — a locked phone suspends `watchPosition` and the
  // drive silently stops being followed.
  useEffect(() => {
    if (!broadcasting) return
    let lock: { release: () => Promise<void> } | null = null
    const nav = navigator as Navigator & {
      wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> }
    }
    const acquire = () => {
      nav.wakeLock
        ?.request("screen")
        .then((l) => {
          lock = l
        })
        .catch(() => {
          /* unsupported or denied — nothing to do but carry on */
        })
    }
    acquire()
    // iOS releases the lock whenever the tab is backgrounded and never
    // restores it. Without re-acquiring, the screen sleeps the first time the
    // driver switches to their nav app, `watchPosition` suspends, and the
    // drive silently stops being followed for the rest of the journey.
    const onVisible = () => {
      if (document.visibilityState === "visible") acquire()
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => {
      document.removeEventListener("visibilitychange", onVisible)
      void lock?.release().catch(() => {})
    }
  }, [broadcasting])

  const submitSoc = useCallback(
    async (soc: number) => {
      if (!runId) return null
      setBusy(true)
      try {
        const st = await recordSoc(runId, soc)
        setState(st)
        return st
      } finally {
        setBusy(false)
      }
    },
    [runId]
  )

  const requestReplan = useCallback(async () => {
    if (!runId) return null
    setBusy(true)
    try {
      const plan = await replanRun(runId)
      setRun((r) => (r ? { ...r, plan } : r))
      return plan
    } finally {
      setBusy(false)
    }
  }, [runId])

  const end = useCallback(async () => {
    if (!runId) return
    setBusy(true)
    try {
      const st = await finishRun(runId)
      setState(st)
      setRun((r) => (r ? { ...r, status: "finished" } : r))
      clearToken(tripId)
      setRunId(null)
    } finally {
      setBusy(false)
    }
  }, [runId, tripId])

  const toggleSharing = useCallback(() => {
    setSharing((s) => {
      const next = !s
      try {
        window.localStorage.setItem(sharingKey(tripId), next ? "on" : "off")
      } catch {
        /* private mode — the toggle still works for this session */
      }
      return next
    })
  }, [tripId])

  return {
    run,
    state,
    localOffsetM,
    isDriver,
    gpsError,
    busy,
    sharing,
    toggleSharing,
    submitSoc,
    requestReplan,
    end,
  }
}
