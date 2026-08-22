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

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Alternatives,
  LiveRoute,
  LiveRun,
  LiveState,
  RerouteResult,
  RevisedPlan,
  ArriveResult,
  acceptStretch,
  arriveAt,
  finishRun,
  getAlternatives,
  getLiveRoute,
  getLiveRun,
  pingRun,
  recordSoc,
  replanRun,
  rerouteRun,
  undoStretch,
  undoArrival,
} from "./client"
import { RouteIndex, buildRouteIndex, project } from "./route"

const PING_EVERY_MS = 25_000
const POLL_EVERY_MS = 10_000
/** How long before the automatic re-route may be attempted again.
 *
 *  It is tried once and then left alone: the condition that triggered it stays
 *  true for as long as the car is off the route, so a failing call — no
 *  signal, a router that is down, the per-run limit — would otherwise be
 *  retried on every render for the rest of the drive. Five minutes is long
 *  enough that a genuine outage costs a handful of attempts, short enough that
 *  a driver who is still lost gets another go without touching anything. */
const REROUTE_COOLDOWN_MS = 5 * 60_000
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
  /** `leaving` says the figure is what the car is pulling out on: it ends the
   *  charge projection rather than restarting it, and takes the car off the
   *  charger. */
  submitSoc: (soc: number, leaving?: boolean) => Promise<LiveState | null>
  /** `exclude` are chargers the driver has turned down; the server keeps them.
   *  `minArrivalSoc` accepts arriving under the reserve on the leg being
   *  driven, to reach a charger further on. */
  requestReplan: (
    exclude?: string[],
    minArrivalSoc?: number | null,
    holdSpeedKph?: number | null
  ) => Promise<RevisedPlan | null>
  /** Other chargers for a stop. Read-only — taking one is a re-plan.
   *  `rejectChargerId` names which stop; omit it for the next one. */
  findAlternatives: (rejectChargerId?: string) => Promise<Alternatives | null>
  /** Tell the drive you are standing at a charger. Uses the phone's position
   *  when it can get one, falling back to `chargerId`. Returns what it
   *  matched, so the caller can say which charger it decided on. */
  markArrived: (chargerId: string | null) => Promise<ArriveResult | null>
  /** Take back the last arrival. The one tap on this screen that no later fix
   *  can correct, so it needs a way out that is not "end the drive". */
  undoArrive: () => Promise<LiveState | null>
  /** "I'll take that stop anyway." Records the accepted floor; does not
   *  re-plan, because the plan already goes there. */
  keepStopAnyway: () => Promise<LiveState | null>
  /** Take that back. The reserve applies again from the next plan onwards. */
  undoKeepStop: () => Promise<LiveState | null>
  /** The road the drive is on, once it is no longer the trip's own. Null until
   *  something has been re-routed, so an ordinary drive fetches no geometry. */
  route: LiveRoute | null
  /** That road, indexed for projection. Whoever draws the journey needs the
   *  same one the fixes are snapped to. */
  routeIndex: RouteIndex | null
  /** Ask for a road from where the car actually is. `force` re-routes even
   *  from ON the route — otherwise refused, because it is the one call that
   *  spends upstream quota and `/replan` answers the same question for free. */
  reroute: (force?: boolean) => Promise<RerouteResult | null>
  /** True while one is in flight, including the automatic one. The screen
   *  cannot show "frozen, rejoin the route" while it is fixing exactly that. */
  rerouting: boolean
  end: () => Promise<void>
}

/** One fix, quickly or not at all. A driver standing at a charger should not
 *  watch a spinner while the phone hunts for satellites — the button has a
 *  charger id to fall back on. */
function currentPosition(): Promise<{ lat: number; lon: number }> {
  return new Promise((resolve, reject) => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      reject(new Error("no geolocation"))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
      reject,
      { enableHighAccuracy: true, timeout: 6000, maximumAge: 30_000 }
    )
  })
}

export function useLiveRun(
  tripId: string,
  /** The trip's own road. Used until the drive is re-routed onto another one,
   *  which this hook is what learns about — so the index is built HERE rather
   *  than handed in, or the caller would need the answer before asking the
   *  question. */
  plannedRoute: { polyline: string; total_dist_m: number },
  initial: LiveRun | null
): LiveHandle {
  const [run, setRun] = useState<LiveRun | null>(initial)
  const [state, setState] = useState<LiveState | null>(initial?.state ?? null)
  const [localOffsetM, setLocalOffsetM] = useState<number | null>(null)
  const [gpsError, setGpsError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [runId, setRunId] = useState<string | null>(null)
  const [sharing, setSharing] = useState(true)
  const [route, setRoute] = useState<LiveRoute | null>(null)
  const [rerouting, setRerouting] = useState(false)
  // Whichever road the car is on. Projecting a fix onto the trip's original
  // polyline after a re-route reports it as permanently off-route on a road it
  // is driving perfectly.
  const routeIndex: RouteIndex | null = useMemo(
    () =>
      buildRouteIndex(
        route?.polyline ?? plannedRoute.polyline,
        route?.total_dist_m ?? plannedRoute.total_dist_m
      ),
    [route, plannedRoute.polyline, plannedRoute.total_dist_m]
  )
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
    // …and the moment the page is looked at again. A phone that was locked
    // has been reporting nothing, so the first thing the driver sees on
    // unlocking is a position from before the gap — and the interval would
    // make them watch it for another 25 seconds. `watchPosition` resumes on
    // its own, so by the time this fires there is usually a fresh fix; when
    // there is not, `send` bails on `a.have` and the interval covers it.
    const onVisible = () => {
      if (document.visibilityState === "visible") void send()
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => {
      alive = false
      clearInterval(h)
      document.removeEventListener("visibilitychange", onVisible)
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
    async (soc: number, leaving = false) => {
      if (!runId) return null
      setBusy(true)
      try {
        // The reading is about here, so it travels with here. The page is in
        // the foreground — the driver is typing into it — so a fix usually
        // arrives even after a long quiet spell at a charger.
        const here = await currentPosition().catch(() => null)
        const st = await recordSoc(runId, soc, here, leaving)
        setState(st)
        setLocalOffsetM(null)
        return st
      } finally {
        setBusy(false)
      }
    },
    [runId]
  )

  const requestReplan = useCallback(
    async (
      exclude: string[] = [],
      minArrivalSoc: number | null = null,
      // `undefined` is "leave my speed alone" and `null` is "you pick" — the
      // same distinction the server draws, carried the whole way rather than
      // collapsed into a default here.
      holdSpeedKph: number | null | undefined = undefined
    ) => {
      if (!runId) return null
      setBusy(true)
      try {
        // A fresh fix first, so the plan starts from where the car is rather
        // than from the last position that reached the server. `maximumAge`
        // means a page that is already reporting pays nothing for this; a
        // phone just unlocked at a services waits a couple of seconds and gets
        // a plan about the road in front of it. Best-effort: no fix simply
        // plans from the last known position, as it always did.
        const here = await currentPosition().catch(() => null)
        const plan = await replanRun(
          runId,
          exclude,
          minArrivalSoc,
          holdSpeedKph,
          here
        )
        setRun((r) => (r ? { ...r, plan } : r))
        return plan
      } finally {
        setBusy(false)
      }
    },
    [runId]
  )

  const findAlternatives = useCallback(
    async (rejectChargerId?: string) => {
      if (!runId) return null
      setBusy(true)
      try {
        return await getAlternatives(runId, rejectChargerId)
      } finally {
        setBusy(false)
      }
    },
    [runId]
  )

  const markArrived = useCallback(
    async (chargerId: string | null) => {
      if (!runId) return null
      setBusy(true)
      try {
        // A fresh fix, if the phone will give one within a few seconds. The
        // page is in the foreground — the driver just pressed a button — so
        // this usually succeeds even though the background watch has been
        // asleep for the whole charge.
        const here = await currentPosition().catch(() => null)
        const res = await arriveAt(runId, chargerId, here)
        setState(res.state)
        // The car has moved; the locally snapped offset is from before the
        // jump and would drag the scene back until the next fix.
        setLocalOffsetM(null)
        return res
      } finally {
        setBusy(false)
      }
    },
    [runId]
  )

  const undoArrive = useCallback(async () => {
    if (!runId) return null
    setBusy(true)
    try {
      const st = await undoArrival(runId)
      setState(st)
      // Same reason as arriving: the car has moved, and the locally snapped
      // offset is from the wrong side of the jump.
      setLocalOffsetM(null)
      return st
    } finally {
      setBusy(false)
    }
  }, [runId])

  const keepStopAnyway = useCallback(async () => {
    if (!runId) return null
    setBusy(true)
    try {
      const st = await acceptStretch(runId)
      setState(st)
      return st
    } finally {
      setBusy(false)
    }
  }, [runId])

  const undoKeepStop = useCallback(async () => {
    if (!runId) return null
    setBusy(true)
    try {
      const st = await undoStretch(runId)
      setState(st)
      return st
    } finally {
      setBusy(false)
    }
  }, [runId])

  const reroute = useCallback(
    async (force = false) => {
      if (!runId) return null
      setRerouting(true)
      try {
        // A fresh fix, and not best-effort like the re-plan's: a re-plan can
        // fall back to the last known position because it slices the road the
        // car is already on. This asks for a road that does not exist yet, and
        // building it from a position twenty minutes stale routes the driver
        // from somewhere they have left.
        const here = await currentPosition()
        const out = await rerouteRun(runId, here, { force })
        setState(out.state)
        setRoute(out.route)
        setRun((r) => (r ? { ...r, plan: out.plan, route_version: out.route.version } : r))
        // The snapped offset is against the OLD polyline. Dropping it hands
        // the screen back to the server's position until the next fix lands on
        // the new road.
        setLocalOffsetM(null)
        return out
      } finally {
        setRerouting(false)
      }
    },
    [runId]
  )

  // The road, once it stops being the trip's own. Fetched on the version
  // moving rather than on a poll, because a polyline is tens of kilobytes and
  // for most drives this never fires at all. Watchers get here too — a page
  // following the link has to draw the road the car actually turned onto.
  const routeVersion = run?.route_version ?? 0
  useEffect(() => {
    if (routeVersion === 0 || route?.version === routeVersion) return
    let alive = true
    getLiveRoute(tripId)
      .then((r) => {
        if (alive) setRoute(r)
      })
      .catch(() => {
        /* the poll will come round again */
      })
    return () => {
      alive = false
    }
  }, [tripId, routeVersion, route?.version])

  // --- driver: off the route for long enough, get a new one ----------------
  //
  // The drive used to freeze every figure and wait for the car to come back,
  // which is the right answer for the thirty seconds it takes to drive round a
  // services and the wrong one for a diversion, a closure, or a nav app that
  // knows about a jam this route does not. The server decides WHEN (two
  // sustained minutes, so a single wild fix cannot spend a directions call);
  // this only carries out the request.
  //
  // The cooldown is the other guard. A re-route that fails — no signal, ORS
  // down, the rate limit — must not be retried on every ping for the rest of
  // the journey, and the flag that says "you are off the route" stays true the
  // whole time it is failing.
  const lastTry = useRef(0)
  useEffect(() => {
    if (!broadcasting || !runId || finished) return
    if (!state?.reroute_suggested || rerouting) return
    if (Date.now() - lastTry.current < REROUTE_COOLDOWN_MS) return
    lastTry.current = Date.now()
    void reroute().catch(() => {
      /* said on screen by the banner, which is still up */
    })
  }, [broadcasting, runId, finished, state?.reroute_suggested, rerouting, reroute])

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
    findAlternatives,
    markArrived,
    keepStopAnyway,
    undoKeepStop,
    route,
    routeIndex,
    reroute,
    rerouting,
    undoArrive,
    end,
  }
}
