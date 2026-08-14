"use client"

/**
 * The right bar: the trips you have planned.
 *
 * The problem it solves is mundane and real — a trip's share link is the only
 * handle it has, so a plan you made last week lives wherever you last pasted
 * it, and people were replanning the same journey rather than going to find it.
 *
 * There is no auth here, and this does not add any. The list comes from the
 * server, keyed on a username the browser claimed with a secret it made up
 * (`lib/account.ts`). Two consequences worth stating, because both are visible
 * in this component:
 *
 *  - **Before a username there is no list**, not an empty one. Trips planned
 *    anonymously are not attached to anything, so the drawer's first state is
 *    an invitation rather than a spinner.
 *  - **Claiming is an opt-in with a consequence**, so the form says the
 *    consequence — the list becomes public at /u/<name> — above the button, not
 *    in a tooltip.
 *
 * State lives in `TripsContext`, not here, because the way in is now a button
 * in `AppHeader` — a different tree. What is left in this file is the panel and
 * the three forms inside it.
 *
 * It comes in from the RIGHT, which is not a preference: it is the edge the
 * button that opens it sits on. A panel that flies out of the opposite side of
 * the screen from the control you just pressed makes you look for it. Same
 * reason the close control points right and the whole thing eases out of that
 * corner rather than simply appearing.
 *
 * An overlay rather than a column that reflows the page. The landing page is a
 * centred hero and the trip page is a chart beside an itinerary; a panel that
 * pushed either of them sideways would be a layout change on every screen for
 * the sake of a list you open occasionally.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react"
import Link from "next/link"
import { toast } from "sonner"
import {
  Check,
  Copy,
  Link2,
  Loader2,
  MapPinned,
  PanelRightClose,
  X,
} from "lucide-react"
import { adoptSecret, ownerSecret, readDeviceCode } from "@/lib/account"
import { claimUsername, releaseUsername, whoAmI } from "@/lib/client"
import DeleteTrip from "../trip/DeleteTrip"
import TripSummaryCard from "./TripSummaryCard"
import { ChipFace, chipSkin } from "./TripsButton"
import { MORPH_MS, useTrips, type OriginRect } from "./TripsContext"

/** The server's rule, mirrored so a bad name fails in the form instead of as a
 *  422 the person has to interpret. `api/users.py` is still the authority. */
const USERNAME_RE = /^[a-z0-9][a-z0-9-]{1,22}[a-z0-9]$/

/** Expo-out. The panel leaves the button fast and settles slowly, which is what
 *  makes a rectangle growing across half the screen read as one object moving
 *  rather than as a box being resized. */
const OPEN_CLIP = "inset(0px 0px 0px 0px round 0px)"
const MORPH_TRANSITION =
  `clip-path ${MORPH_MS}ms cubic-bezier(0.16, 1, 0.3, 1),` +
  ` opacity 180ms ease-out, visibility ${MORPH_MS}ms`

/**
 * The panel grows out of the button that opened it.
 *
 * A container transform, done with `clip-path` rather than by animating a
 * clone: the panel is already in the DOM at its final size, so the only thing
 * changing is how much of it is revealed, and nothing has to be measured,
 * duplicated and swapped at the end. The shape starts as a pill the size and
 * shape of the chip, sitting exactly where the chip is, and opens to the full
 * panel.
 *
 * A shape on its own is not a morph, though, and the first version proved it:
 * clipping a white panel to a pill gives you a blank pill with a sliver of the
 * close button in it, so what you saw was the chip vanishing and an empty
 * lozenge growing where it had been. The missing half is the CONTENT handover,
 * which is why this returns `ghost` — the rectangle, in panel-local
 * coordinates, where a copy of the chip's face is drawn inside the panel. That
 * copy holds the first ~140ms, the panel's real contents fade up behind it, and
 * the button itself is hidden outright rather than faded. Three surfaces, one
 * apparent object.
 *
 * Two conditions, and it silently falls back to the plain slide otherwise:
 *
 *  - **Motion is welcome.** `prefers-reduced-motion` is asking for less of
 *    exactly this.
 *  - **We know where the button was.** Escape and the scrim close without ever
 *    having touched one; on a page that opened from a stale rect the slide is
 *    a better answer than a morph out of the wrong corner.
 *
 * Screen size is deliberately NOT one of them. The same chip in the same
 * corner opens the same panel on a phone, and a small control growing into a
 * full screen is the transition phones are built around — a sheet sliding up
 * from nowhere is the compromise, not the native idea. What the phone changes
 * is the surface underneath (see the panel's background classes), because the
 * cost of this animation is not its geometry.
 *
 * The panel geometry is computed rather than measured — `offsetWidth` and the
 * viewport, never `getBoundingClientRect()`. The closed panel carries the
 * fallback's `translate-x-full`, so its measured rectangle is a screen's width
 * to the right of where it will actually be.
 *
 * The opening is a hand-rolled FLIP and it is written imperatively for a
 * reason: the collapsed shape has to be COMMITTED before the expanded one is
 * set, or the browser coalesces both into one style change and there is
 * nothing to transition from. `void el.offsetWidth` forces that commit
 * synchronously. The obvious React version — set the collapsed shape, wait two
 * animation frames, set the expanded one — was written first and is wrong in a
 * way worth recording: in a tab that is not being painted, nested
 * `requestAnimationFrame` callbacks never run, so the panel opened as a pill
 * and stayed one until the tab was looked at. A reflow does not care whether
 * anybody is watching.
 */
function usePanelMorph(
  open: boolean,
  origin: OriginRect | null,
  morphing: boolean
) {
  const ref = useRef<HTMLDivElement>(null)
  // Where the chip's copy goes, in the panel's own coordinates. Stored from
  // the same measurement that builds the clip, so the copy and the shape it
  // sits in can never disagree by a pixel — computing it twice is how a
  // container transform ends up with a face floating next to its container.
  const [ghost, setGhost] = useState<OriginRect | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if (!morphing || !origin) {
      el.style.clipPath = ""
      el.style.transition = ""
      if (open) el.style.transform = ""
      setGhost(null)
      return
    }
    // Measured, not derived. Deriving the panel's left edge from the viewport
    // width cost two bugs: `window.innerWidth` includes the classic scrollbar
    // and a `fixed right-0` element is laid out without it, so everything sat
    // 5px left on any page long enough to scroll; and `offsetWidth` is rounded
    // to whole pixels, so at `90vw` on a 375px phone — 337.5 — it lied by half
    // a pixel. The rect knows both. It is only trustworthy because the panel
    // carries NO transform while morphing (the slide's classes are off, which
    // is what `morphing` guarantees) — under the fallback this same call would
    // return a box a screen's width to the right.
    const box = el.getBoundingClientRect()
    const x = origin.left - box.left
    const y = origin.top - box.top
    const w = box.width
    const h = box.height
    const collapsed = `inset(${y}px ${w - (x + origin.width)}px ${
      h - (y + origin.height)
    }px ${x}px round ${origin.height / 2}px)`
    // The clip is measured from the panel's BORDER box; an absolutely
    // positioned child is placed from its PADDING box. The panel has a 1px
    // left border, so the copy needs that back or it sits one pixel inside the
    // shape it is supposed to be filling.
    setGhost({
      top: y - el.clientTop,
      left: x - el.clientLeft,
      width: origin.width,
      height: origin.height,
    })

    if (!open) {
      el.style.transition = MORPH_TRANSITION
      el.style.clipPath = collapsed
      return
    }
    // A swipe that dismissed the panel left it parked off to the right, and it
    // is deliberately not cleaned up there — clearing it mid-close would snap
    // the panel back into view for the length of its fade. Opening is where it
    // gets undone, before anything is measured.
    el.style.transform = ""
    el.style.transition = "none"
    el.style.clipPath = collapsed
    void el.offsetWidth
    el.style.transition = MORPH_TRANSITION
    el.style.clipPath = OPEN_CLIP
  }, [open, origin, morphing])

  return { ref, ghost }
}

/** How far right counts as "take it away" — a third of the panel is a
 *  deliberate commitment. It is measured against where the gesture is GOING,
 *  not where the finger let go (see `project`), which is what lets a short fast
 *  flick dismiss and a long slow drag think better of it. */
const DISMISS_FRACTION = 0.32
const SWIPE_SLOP = 10 // px before a touch is a drag and not a tap
const SETTLE_MS = 200
const SETTLE_CURVE = "cubic-bezier(0.16, 1, 0.3, 1)"
/** Initial slope of `SETTLE_CURVE` — dy/dx at t=0 is y1/x1 = 1/0.16. Used to
 *  solve the settle duration that makes the animation LEAVE the finger at the
 *  speed the finger was moving. */
const SETTLE_SLOPE = 1 / 0.16
const SETTLE_MIN_MS = 120
const SETTLE_MAX_MS = 420

/**
 * Where a flick is going to end up, given how fast it was travelling.
 *
 * Exponential decay, the same shape a scroll view decelerates with — NOT the
 * textbook `v²/2a`. `v` here is px per ms, so the rate factor is applied
 * directly; at 0.998 a release at 0.5 px/ms projects about 250px, which on a
 * 340px panel is the difference between a flick that lets go at 10% and a
 * dismissal.
 */
function project(v: number, decelerationRate = 0.998) {
  return (v * decelerationRate) / (1 - decelerationRate)
}

/**
 * Progressive resistance past a boundary, instead of a wall.
 *
 * Dragging LEFT has nothing to reveal — the panel is against the right edge and
 * the page is behind it. The honest answer to that is not to ignore the finger
 * (which reads as the panel having frozen, i.e. as the gesture having broken)
 * but to follow it less and less. `dimension` is the budget the resistance
 * asymptotes to, so a hard shove opens a tenth of the panel and no more.
 */
function rubberband(overshoot: number, dimension: number, constant = 0.55) {
  return (
    (overshoot * dimension * constant) /
    (dimension + constant * Math.abs(overshoot))
  )
}

/** The panel's live on-screen X, mid-animation if it is mid-animation.
 *  `getComputedStyle` reports the PRESENTATION value during a transition, which
 *  is the whole point: a gesture that starts here starts from what the eye can
 *  see, not from where the last animation was aiming. */
function presentationX(el: HTMLElement) {
  const t = getComputedStyle(el).transform
  if (!t || t === "none") return 0
  try {
    return new DOMMatrixReadOnly(t).m41
  } catch {
    return 0
  }
}

/**
 * Push the panel off to the right with your thumb.
 *
 * Touch only. A mouse has the scrim, the close button and Escape; a pointer
 * drag on a desktop is how you select text, and taking that over to duplicate
 * three existing affordances is a bad trade. On a phone the panel is 90% of
 * the screen, the close control is a small target in the far corner, and this
 * is how every sheet on the device already behaves.
 *
 * It moves with a `transform`, not with the clip the morph animates. The clip
 * is a window onto the panel, so shrinking it during a drag would wipe the
 * contents away rather than move them, and the shape would stop matching where
 * your finger is. Transform moves the whole thing, which is what a hand does.
 *
 * `touch-action: pan-y` on the panel is what makes this coexist with the list
 * inside it: the browser keeps vertical scrolling and hands us horizontal, so
 * scrolling never stutters waiting to find out whether a gesture was a swipe.
 * The axis check is still needed for the diagonal start.
 */
function useSwipeDismiss(
  panelRef: React.RefObject<HTMLDivElement | null>,
  scrimRef: React.RefObject<HTMLDivElement | null>,
  open: boolean,
  onDismiss: () => void
) {
  const drag = useRef<{
    id: number
    x0: number
    y0: number
    base: number
    x: number
    t: number
    v: number
    axis: "none" | "x" | "y"
    w: number
  } | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current)
    },
    []
  )

  const paint = (x: number) => {
    const el = panelRef.current
    if (!el) return
    el.style.transition = "none"
    el.style.transform = `translateX(${x}px)`
    const scrim = scrimRef.current
    if (scrim && drag.current) {
      // Clamped at both ends: a rubber-banded drag to the left produces a
      // negative x, and an un-clamped ratio would push the scrim past opaque.
      scrim.style.opacity = String(
        Math.min(1, Math.max(0, 1 - x / drag.current.w))
      )
    }
  }

  /**
   * Hand the gesture over to an animation without a seam.
   *
   * A fixed-duration settle is the seam: let go at speed and the panel visibly
   * slows to somebody else's timing at the moment your thumb leaves it. There
   * is no initial-velocity input on a CSS transition, so the duration is solved
   * for instead — with the curve's initial slope known, `distance · slope / v`
   * is the duration whose first frame moves at exactly `v`. Clamped, because a
   * near-zero release would ask for an animation minutes long, and only used
   * when the release was actually travelling towards the target; a settle that
   * reverses direction has no velocity to inherit.
   */
  const settleDuration = (distance: number, v: number, towards: number) => {
    if (distance <= 0.5) return SETTLE_MIN_MS
    const speed = Math.sign(v) === Math.sign(towards) ? Math.abs(v) : 0
    if (speed < 0.05) return SETTLE_MS
    return Math.min(
      SETTLE_MAX_MS,
      Math.max(SETTLE_MIN_MS, (distance * SETTLE_SLOPE) / speed)
    )
  }

  const end = (dismiss: boolean, from: number, v: number) => {
    const el = panelRef.current
    const scrim = scrimRef.current
    const w = drag.current?.w ?? 0
    drag.current = null
    if (!el) return
    const to = dismiss ? w : 0
    const ms = settleDuration(Math.abs(to - from), v, to - from)
    el.style.transition = `transform ${ms}ms ${SETTLE_CURVE}`
    el.style.transform = `translateX(${to}px)`
    if (scrim) {
      scrim.style.transition = `opacity ${ms}ms ease-out`
      scrim.style.opacity = dismiss ? "0" : "1"
    }
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      timer.current = null
      if (dismiss) {
        onDismiss()
      } else if (el) {
        // Back where it started, and the inline styles handed back so the
        // classes and the morph own the element again.
        el.style.transform = ""
        el.style.transition = ""
      }
      if (scrim) {
        scrim.style.transition = ""
        scrim.style.opacity = ""
      }
    }, ms)
  }

  return {
    // `touch-action` is set in the class list, not here — this only decides
    // what to do with the events the browser has already agreed to send.
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      if (!open || e.pointerType !== "touch") return
      const el = panelRef.current
      if (!el) return
      // A panel that is already settling is caught where it is, not where it
      // was heading. Three things have to happen together or the grab reads as
      // a jump: the pending settle is cancelled (it would otherwise fire
      // `onDismiss` under the finger), the live transform is frozen in place,
      // and the gesture measures from that value rather than from zero.
      const base = presentationX(el)
      if (timer.current) {
        window.clearTimeout(timer.current)
        timer.current = null
      }
      el.style.transition = "none"
      el.style.transform = `translateX(${base}px)`
      const scrim = scrimRef.current
      if (scrim) scrim.style.transition = "none"
      drag.current = {
        id: e.pointerId,
        x0: e.clientX,
        y0: e.clientY,
        base,
        x: e.clientX,
        t: e.timeStamp,
        v: 0,
        axis: "none",
        w: el.getBoundingClientRect().width,
      }
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = drag.current
      if (!d || e.pointerId !== d.id) return
      const dx = e.clientX - d.x0
      const dy = e.clientY - d.y0
      if (d.axis === "none") {
        if (Math.abs(dy) > SWIPE_SLOP && Math.abs(dy) > Math.abs(dx)) {
          // A scroll. Let go of it completely rather than watching it, or a
          // fast flick down that drifts sideways starts moving the panel.
          drag.current = null
          return
        }
        if (Math.abs(dx) < SWIPE_SLOP) return
        d.axis = "x"
        try {
          panelRef.current?.setPointerCapture(d.id)
        } catch {
          // The pointer can already be gone by the time we decide it was a
          // drag. Capture is an improvement on the gesture, not a requirement:
          // without it the events keep coming from the element itself.
        }
      }
      // Velocity over a real window, never over the last pair of events. A
      // browser can deliver several moves inside one millisecond — coalesced
      // input, a 120Hz digitiser — and dividing a 50px jump by a 1ms floor
      // produces a number that clears any flick threshold you could pick, so
      // the panel flies away on a twitch. Below the window the previous
      // reading stands, which is what a hand is doing anyway.
      const dt = e.timeStamp - d.t
      if (dt >= 8) {
        d.v = (e.clientX - d.x) / dt
        d.x = e.clientX
        d.t = e.timeStamp
      }
      // Rightwards is free travel; leftwards resists. There is nothing behind
      // the panel on that side to reveal, but refusing to move at all is what
      // makes a gesture feel like it has hit a fault rather than a limit.
      const x = d.base + dx
      paint(x >= 0 ? x : rubberband(x, d.w * 0.1))
    },
    onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = drag.current
      if (!d || e.pointerId !== d.id) return
      if (d.axis !== "x") {
        drag.current = null
        return
      }
      const x = Math.max(0, d.base + (e.clientX - d.x0))
      // The drag ends on whatever was under the thumb — a trip card, usually,
      // which is a link. Swallow the click this gesture is about to produce.
      const swallow = (ev: MouseEvent) => {
        ev.preventDefault()
        ev.stopPropagation()
      }
      window.addEventListener("click", swallow, { capture: true, once: true })
      window.setTimeout(
        () => window.removeEventListener("click", swallow, { capture: true }),
        0
      )
      // Decided on where the gesture is going, not on where it stopped. One
      // test replaces the old distance-OR-speed pair, and it is the same test
      // a scroll view makes: carry the release velocity forward to a resting
      // point, then ask which side of the threshold that lands on.
      end(x + project(d.v) > d.w * DISMISS_FRACTION, x, d.v)
    },
    onPointerCancel: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = drag.current
      if (!d || e.pointerId !== d.id) return
      if (d.axis !== "x") {
        drag.current = null
        return
      }
      const el = panelRef.current
      end(false, el ? presentationX(el) : 0, 0)
    },
  }
}

export default function TripsDrawer() {
  const {
    mounted,
    open,
    toggle,
    origin,
    morphing,
    username,
    count,
    trips,
    loading,
    adopt,
    release,
    dropTrip,
  } = useTrips()
  const { ref: panelRef, ghost } = usePanelMorph(open, origin, morphing)
  const scrimRef = useRef<HTMLDivElement>(null)
  const swipe = useSwipeDismiss(panelRef, scrimRef, open, () => toggle(false))

  // How each of the panel's three bands arrives. Under the morph they crossfade
  // in behind the chip's copy — a stagger there fights the container, which is
  // already the thing carrying the eye. Under the slide they cascade, because
  // a panel that arrives whole and instantly full is the flat version.
  //
  // `bandKey` is separate from the rest because React refuses a `key` that
  // arrives through a spread — it is not a prop — and silently treats it as
  // one, which turns the cascade into a one-time animation that never plays
  // again. It has to be written on the element by hand.
  const bandKey = (i: number) =>
    morphing ? `band-${i}` : open ? `band-${i}-in` : `band-${i}`
  const band = (i: number) =>
    morphing
      ? {
          style: { transitionDelay: open ? `${130 + i * 45}ms` : "0ms" },
          className: `transition-opacity duration-200 ${
            open ? "opacity-100" : "opacity-0"
          }`,
        }
      : {
          // Keyed so the animation re-runs on every open: a CSS animation fires
          // once per element and this panel is never unmounted. Keyed on `open`
          // rather than a counter, so the closing pass renders the plain
          // version — content rising into view while the panel leaves is the
          // animation playing backwards at the wrong moment.
          style: { animationDelay: `${60 + i * 70}ms` },
          className: open ? "animate-panel-rise" : "",
        }

  // Escape closes it, like every other overlay on the web.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggle(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, toggle])

  if (!mounted) return null

  return (
    <>
      {/* Clicking away closes it, on every screen. It used to be `lg:hidden`,
          which left the desktop with a panel you could only dismiss by finding
          one of two small buttons — and the first thing anybody does with an
          overlay they are finished with is click the page behind it.
          Lighter over a wide screen than over a phone: there the panel takes
          the whole display and the wash is what separates it from the page,
          here it only has to say "this is on top" and catch the click.
          Always mounted, so it can fade rather than blink. */}
      <div
        ref={scrimRef}
        onClick={() => toggle(false)}
        aria-hidden
        className={`fixed inset-0 z-40 bg-ink-900/30 transition-opacity duration-200 sm:bg-ink-900/20 sm:backdrop-blur-[1px] lg:bg-ink-900/10 lg:backdrop-blur-0 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-label="Your trips"
        aria-hidden={!open}
        {...swipe}
        // z-50, above the scrim's z-40.
        //
        // Frosted rather than solid, FROM `sm` UP. On the trip page this opens
        // over the 3D journey scene, and a flat white column there cuts the
        // picture in two; letting the scene blur through keeps the panel
        // reading as something laid ON the page rather than a hole punched in
        // it. The cards inside stay opaque, so the text they carry never sits
        // on a moving image.
        //
        // On a phone it is solid, and that is the animation's doing, not a
        // change of taste. A backdrop filter is re-computed over whatever the
        // element covers, every frame; here the element is 90vw × 100dvh and
        // its shape is being animated for 460ms, which is the expensive corner
        // of the expensive case, on the weakest hardware. There is also nothing
        // to be frosted over — at 90vw the page is a 10% strip — so the phone
        // pays the whole cost for an effect it cannot see. The scrim drops its
        // blur there for the same reason and carries a slightly heavier wash
        // instead, which is what that blur was doing for it.
        //
        // Two ways in. `usePanelMorph` grows it out of the button on a laptop;
        // everywhere else it leaves and returns from the top-right corner, the
        // slide carrying it to the edge and the scale — origin pinned at that
        // corner — aiming it at the button rather than at the whole edge. Small
        // (2%) on purpose; anything more reads as a zoom and wobbles against
        // the translation. The two must not both run: a panel that slides in
        // from off-screen WHILE a hole opens in it is two animations arguing.
        //
        // `invisible` when closed, and visibility is in the transition list on
        // purpose: it is a discrete property, so it holds `visible` for the
        // whole animation and flips at the end — the panel still leaves, and
        // once it has, its inputs and links are out of the tab order instead of
        // being focusable off-screen behind an `aria-hidden`.
        className={`fixed right-0 top-0 z-50 flex h-dvh w-[min(22rem,90vw)] origin-top-right touch-pan-y flex-col border-l border-ink-100/80 bg-white shadow-xl sm:bg-white/80 sm:backdrop-blur-xl ${
          morphing ? "" : "transition-[transform,opacity,visibility] duration-200 ease-out"
        } ${
          open
            ? `visible opacity-100 ${morphing ? "" : "translate-x-0 scale-100"}`
            : `invisible opacity-0 ${morphing ? "" : "translate-x-full scale-[0.98]"}`
        }`}
      >
        {/* The chip, redrawn inside the shape that is growing out of it. It is
            the outgoing half of the crossfade: on screen at full strength for
            the frame where the panel IS the chip, gone by 140ms, and back at
            the end of the close so there is something in the pill when the real
            button reappears under the cursor. `pointer-events-none` because it
            is scenery — the panel behind it is what the click lands on. */}
        {ghost && (
          <div
            aria-hidden
            style={{
              position: "absolute",
              top: ghost.top,
              left: ghost.left,
              width: ghost.width,
              height: ghost.height,
              transitionDelay: open ? "0ms" : `${MORPH_MS - 220}ms`,
            }}
            className={`${chipSkin(
              username !== null
            )} pointer-events-none justify-center overflow-hidden transition-opacity duration-150 ${
              open ? "opacity-0" : "opacity-100"
            }`}
          >
            <ChipFace
              claimed={username !== null}
              label={username !== null ? `@${username}` : "Your trips"}
              count={count}
            />
          </div>
        )}

        {/* Safe areas top and bottom: this panel is full-height on a phone, so
            without them the title sits under the notch and the release control
            sits under the home indicator. `FeedbackLink` does the same. */}
        <header
          key={bandKey(0)}
          style={band(0).style}
          className={`flex items-center justify-between border-b border-ink-100/80 px-4 py-3 pt-[max(0.75rem,env(safe-area-inset-top))] ${
            band(0).className
          }`}
        >
          <h2 className="font-display text-base font-semibold text-ink-900">
            Your trips
          </h2>
          <button
            type="button"
            onClick={() => toggle(false)}
            aria-label="Close your trips"
            className="rounded-lg p-1.5 text-ink-400 transition hover:bg-ink-50 hover:text-ink-700"
          >
            <PanelRightClose className="h-5 w-5" />
          </button>
        </header>

        <div
          key={bandKey(1)}
          style={band(1).style}
          className={`flex-1 overflow-y-auto px-4 py-4 ${band(1).className}`}
        >
          {username === null ? (
            <ClaimForm onClaimed={adopt} />
          ) : loading && trips === null ? (
            <p className="flex items-center gap-2 text-sm text-ink-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading your trips…
            </p>
          ) : trips && trips.length > 0 ? (
            <ul className="grid gap-3">
              {trips.map((t) => (
                <TripSummaryCard
                  key={t.id}
                  trip={t}
                  // Deleting from the list rather than only from the trip page:
                  // this panel exists because people lose track of what they
                  // planned, and tidying up is the other half of that. Dropped
                  // from local state rather than refetched — the row is gone,
                  // and a round trip to prove it would only make it flicker.
                  action={
                    <DeleteTrip
                      tripId={t.id}
                      compact
                      onDeleted={() => dropTrip(t.id)}
                    />
                  }
                />
              ))}
            </ul>
          ) : (
            <div className="rounded-2xl border border-dashed border-ink-200 p-4 text-sm text-ink-500">
              <MapPinned className="mb-2 h-5 w-5 text-brand-500" />
              Nothing here yet. The next trip you plan appears in this list — and
              on your public page.
            </div>
          )}
        </div>

        {username !== null && (
          <div key={bandKey(2)} style={band(2).style} className={band(2).className}>
            <DrawerFooter
              username={username}
              onReleased={release}
              onAdopted={adopt}
            />
          </div>
        )}
      </div>
    </>
  )
}

/** The opt-in. Everything the claim does is stated before the button. */
function ClaimForm({ onClaimed }: { onClaimed: (name: string) => void }) {
  // The box holds the string that was typed, not a sanitised version of it —
  // rewriting on every keystroke is how "marc-" becomes unreachable while
  // you're still typing "marc-2026".
  const [value, setValue] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)

  const name = value.trim().toLowerCase()
  const valid = USERNAME_RE.test(name)
  const showInvalid = value.length > 0 && !valid

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!valid || busy) return
    setBusy(true)
    setError(null)
    try {
      // The one place a secret is minted. Not on page load: somebody who never
      // opts in should have nothing stored.
      ownerSecret(true)
      const profile = await claimUsername(name)
      toast.success(`“${profile.username}” is yours`)
      onClaimed(profile.username)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not claim that name")
    } finally {
      setBusy(false)
    }
  }

  if (linking) {
    return <LinkDeviceForm onDone={onClaimed} onCancel={() => setLinking(false)} />
  }

  return (
    <form onSubmit={submit}>
      <p className="text-sm leading-relaxed text-ink-600">
        Pick a username and every trip you plan from now on is kept here, on any
        device you sign this browser into.
      </p>
      <label
        htmlFor="evtrip-username"
        className="mt-4 block text-xs font-medium uppercase tracking-wide text-ink-400"
      >
        Username
      </label>
      <input
        id="evtrip-username"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        // "e.g." on purpose. A bare name in an empty box reads as a value that
        // is already filled in rather than a hint, and this is the one field
        // where mistaking the two means claiming a public handle you did not
        // choose. The example doubles as the character set: lowercase and
        // dashes.
        placeholder="e.g. alpine-driver"
        autoComplete="off"
        spellCheck={false}
        aria-invalid={showInvalid}
        className={`mt-1 w-full rounded-xl border px-3 py-2 text-sm outline-none transition focus:ring-2 ${
          showInvalid
            ? "border-red-300 bg-red-50 focus:ring-red-200"
            : "border-ink-200 focus:border-brand-400 focus:ring-brand-100"
        }`}
      />
      {showInvalid && (
        <p className="mt-1 text-xs text-red-700">
          3–24 characters: lowercase letters, numbers and dashes, not starting or
          ending with a dash.
        </p>
      )}

      <p className="mt-3 rounded-xl border border-brand-200 bg-brand-50 px-3 py-2 text-xs leading-relaxed text-brand-800">
        This makes a <strong>public page</strong> at{" "}
        <span className="font-mono">/u/{valid ? name : "yourname"}</span>. Anyone
        who knows the name can see the town you left, where you were going, the
        distance and the car — never your exact address. Trips you planned before
        now are not added, and you can release the name at any time.
      </p>

      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}

      <button
        type="submit"
        disabled={!valid || busy}
        className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-50"
      >
        {busy && <Loader2 className="h-4 w-4 animate-spin" />}
        Claim this username
      </button>

      <button
        type="button"
        onClick={() => setLinking(true)}
        className="mt-3 w-full text-center text-xs text-ink-500 underline underline-offset-2 hover:text-ink-800"
      >
        Already have one on another device?
      </button>
    </form>
  )
}

/** Paste the code from the device that holds the username. */
function LinkDeviceForm({
  onDone,
  onCancel,
}: {
  onDone: (name: string) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // Ask first, store second. Adopting replaces this browser's own secret,
      // so doing it before the check turns a mistyped code into the loss of
      // whatever username this device already had.
      const code = readDeviceCode(value)
      if (!code) {
        setError("That does not look like a device code.")
        return
      }
      const profile = await whoAmI(code)
      if (!profile) {
        // Says where to look, because both ways of getting here are about
        // WHICH browser the code came from: one that never picked a name, or
        // one on a different copy of the site. Nothing has been changed here.
        setError(
          "That code is a valid one, but no username is attached to it. " +
            "Copy it from the browser that shows your username, on this same site."
        )
        return
      }
      if (!adoptSecret(code)) {
        setError("This browser will not let us store the code.")
        return
      }
      toast.success(`Signed in as “${profile.username}”`)
      onDone(profile.username)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not use that code")
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="text-sm leading-relaxed text-ink-600">
        On the device that has your trips, open this panel and choose{" "}
        <strong>Link another device</strong>. Paste the code here.
      </p>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="00000000-0000-4000-8000-000000000000"
        autoComplete="off"
        spellCheck={false}
        className="mt-3 w-full rounded-xl border border-ink-200 px-3 py-2 font-mono text-xs outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
      />
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={busy || !value.trim()}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-brand-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-50"
        >
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          Use this code
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-xl px-3 py-2.5 text-sm text-ink-600 underline underline-offset-2"
        >
          Back
        </button>
      </div>
    </form>
  )
}

/** Share it, move it to another device, or end it. */
function DrawerFooter({
  username,
  onReleased,
  onAdopted,
}: {
  username: string
  onReleased: () => void
  onAdopted: (name: string) => void
}) {
  const [revealing, setRevealing] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [linking, setLinking] = useState(false)
  const [copied, setCopied] = useState<"link" | "code" | null>(null)
  const [busy, setBusy] = useState(false)

  async function copy(what: "link" | "code", text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
      window.setTimeout(() => setCopied(null), 1500)
    } catch {
      toast.error("Could not copy — select it by hand")
    }
  }

  async function confirmRelease() {
    setBusy(true)
    try {
      await releaseUsername(username)
      toast.success("Username released")
      onReleased()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not release it")
    } finally {
      setBusy(false)
      setReleasing(false)
    }
  }

  if (linking) {
    return (
      <div className="border-t border-ink-100 px-4 py-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <LinkDeviceForm onDone={onAdopted} onCancel={() => setLinking(false)} />
      </div>
    )
  }

  const profileUrl =
    typeof window !== "undefined" ? `${window.location.origin}/u/${username}` : ""

  return (
    <div className="border-t border-ink-100/80 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      <div className="flex items-center justify-between gap-2">
        <Link
          href={`/u/${username}`}
          className="truncate font-mono text-xs text-ink-500 underline underline-offset-2 hover:text-brand-700"
        >
          /u/{username}
        </Link>
        <button
          type="button"
          onClick={() => void copy("link", profileUrl)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-ink-200 px-2 py-1 text-xs text-ink-600 transition hover:border-brand-300 hover:text-brand-700"
        >
          {copied === "link" ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {copied === "link" ? "Copied" : "Share"}
        </button>
      </div>

      {revealing ? (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3">
          <p className="text-xs leading-relaxed text-amber-900">
            Anyone with this code can plan trips as you and release the name.
            Treat it like a password.
          </p>
          <code className="mt-2 block break-all rounded-lg bg-white px-2 py-1.5 font-mono text-[0.7rem] text-ink-700">
            {ownerSecret(false) ?? "—"}
          </code>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void copy("code", ownerSecret(false) ?? "")}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-2.5 py-1 text-xs font-semibold text-white"
            >
              {copied === "code" ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
              {copied === "code" ? "Copied" : "Copy code"}
            </button>
            <button
              type="button"
              onClick={() => setRevealing(false)}
              className="px-2 py-1 text-xs text-amber-900 underline underline-offset-2"
            >
              Hide
            </button>
          </div>
        </div>
      ) : releasing ? (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3">
          <p className="text-xs leading-relaxed text-red-900">
            Release “{username}”? The public page goes immediately and the trips
            are detached from it for good. The trips themselves and their share
            links are kept — delete those from the trip itself.
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void confirmRelease()}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-60"
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Release it
            </button>
            <button
              type="button"
              onClick={() => setReleasing(false)}
              className="px-2 py-1 text-xs text-ink-600 underline underline-offset-2"
            >
              Keep it
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <button
            type="button"
            onClick={() => setRevealing(true)}
            className="inline-flex items-center gap-1 text-ink-500 underline underline-offset-2 hover:text-ink-800"
          >
            <Link2 className="h-3.5 w-3.5" />
            Link another device
          </button>
          <button
            type="button"
            onClick={() => setLinking(true)}
            className="text-ink-500 underline underline-offset-2 hover:text-ink-800"
          >
            Use a code
          </button>
          <button
            type="button"
            onClick={() => setReleasing(true)}
            className="inline-flex items-center gap-1 text-ink-400 underline underline-offset-2 hover:text-red-700"
          >
            <X className="h-3.5 w-3.5" />
            Release
          </button>
        </div>
      )}
    </div>
  )
}
