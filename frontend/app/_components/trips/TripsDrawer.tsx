"use client"

/**
 * The left bar: the trips you have planned.
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
 * Mounted-guard, like `CountingToggle`: the username lives in localStorage,
 * which the server cannot see, so the first client render has to match the
 * server's "nothing yet" and correct itself a frame later.
 *
 * An overlay rather than a column that reflows the page. The landing page is a
 * centred hero and the trip page is a chart beside an itinerary; a panel that
 * pushed either of them sideways would be a layout change on every screen for
 * the sake of a list you open occasionally.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { toast } from "sonner"
import {
  Check,
  Copy,
  Link2,
  Loader2,
  MapPinned,
  PanelLeftClose,
  Route,
  X,
} from "lucide-react"
import {
  adoptSecret,
  forgetUsername,
  ownerSecret,
  setStoredUsername,
  storedUsername,
} from "@/lib/account"
import {
  claimUsername,
  getUserTrips,
  releaseUsername,
  whoAmI,
  type TripSummary,
} from "@/lib/client"
import TripSummaryCard from "./TripSummaryCard"

const OPEN_KEY = "evtrip.drawer"

/** The server's rule, mirrored so a bad name fails in the form instead of as a
 *  422 the person has to interpret. `api/users.py` is still the authority. */
const USERNAME_RE = /^[a-z0-9][a-z0-9-]{1,22}[a-z0-9]$/

export default function TripsDrawer() {
  const [mounted, setMounted] = useState(false)
  const [open, setOpen] = useState(false)
  // Whether the collapsed desktop tab is showing its label. Held in state
  // rather than done with a `sm:group-hover:` class because only one of the two
  // width classes is then ever on the element — no stacked variant to get the
  // cascade order right, and the open/closed states are testable.
  const [tabExpanded, setTabExpanded] = useState(false)
  const [username, setUsername] = useState<string | null>(null)
  const [trips, setTrips] = useState<TripSummary[] | null>(null)
  const [loading, setLoading] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMounted(true)
    setUsername(storedUsername())
    try {
      setOpen(window.localStorage.getItem(OPEN_KEY) === "1")
    } catch {
      /* a browser that will not remember the panel state just starts closed */
    }
  }, [])

  const toggle = useCallback((next: boolean) => {
    setOpen(next)
    try {
      window.localStorage.setItem(OPEN_KEY, next ? "1" : "0")
    } catch {
      /* not worth a thought */
    }
  }, [])

  const refresh = useCallback(async (name: string) => {
    setLoading(true)
    try {
      const data = await getUserTrips(name)
      setTrips(data.trips)
    } catch {
      // A released name, or a browser that remembers one the server does not.
      // Falling back to the claim state is better than an error nobody can act
      // on — and `forgetUsername` keeps the secret, so claiming again is one
      // step rather than a new identity.
      setTrips(null)
      setUsername(null)
      forgetUsername()
    } finally {
      setLoading(false)
    }
  }, [])

  // Refetch whenever the panel is opened: the common path into this drawer is
  // "I just planned a trip", and a list cached from before that would be
  // missing the one trip the person came looking for.
  useEffect(() => {
    if (open && username) void refresh(username)
  }, [open, username, refresh])

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
      {!open && (
        <button
          type="button"
          onClick={() => toggle(true)}
          onMouseEnter={() => setTabExpanded(true)}
          onMouseLeave={() => setTabExpanded(false)}
          onFocus={() => setTabExpanded(true)}
          onBlur={() => setTabExpanded(false)}
          aria-label="Open your trips"
          title="Your trips"
          // Two shapes, because a left-edge tab works on a wide screen and
          // fights a phone. Mobile content is full-bleed, so an edge tab lands
          // on the hero wherever it is put vertically — there it is a labelled
          // pill floating bottom-RIGHT, clear of `FeedbackLink` in the opposite
          // corner. From `sm` up it is the edge tab the panel slides out of.
          //
          // On the desktop tab the label is collapsed until hover. This sits
          // over the trip page's dark journey scene, and a permanent opaque
          // pill there reads as damage to the picture rather than as chrome —
          // so it stays a translucent icon until you go for it. The label is
          // still in `aria-label` and `title`, and the mobile pill keeps it
          // visible, which is where discovering it actually matters.
          className={`fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-4 z-30 flex items-center gap-2 rounded-full border border-ink-200 bg-white/90 px-4 py-3 text-sm font-medium text-ink-600 shadow-lg shadow-ink-900/10 backdrop-blur transition-[background-color,border-color,color,padding] hover:border-brand-300 hover:text-brand-700 sm:bottom-auto sm:left-0 sm:right-auto sm:top-24 sm:rounded-l-none sm:rounded-r-xl sm:border-l-0 sm:py-2.5 sm:pl-2 sm:shadow-sm ${
            tabExpanded
              ? "sm:gap-2 sm:border-ink-200 sm:bg-white/95 sm:pr-3"
              : "sm:gap-0 sm:border-ink-200/60 sm:bg-white/55 sm:pr-2"
          }`}
        >
          <Route className="h-4 w-4 shrink-0" />
          <span
            className={`overflow-hidden whitespace-nowrap transition-[max-width,opacity] duration-200 ${
              tabExpanded ? "sm:max-w-24 sm:opacity-100" : "sm:max-w-0 sm:opacity-0"
            }`}
          >
            Your trips
          </span>
        </button>
      )}

      {open && (
        <div
          className="fixed inset-0 z-40 bg-ink-900/20 backdrop-blur-[1px] lg:hidden"
          onClick={() => toggle(false)}
          aria-hidden
        />
      )}

      <div
        ref={panelRef}
        role="dialog"
        aria-label="Your trips"
        aria-hidden={!open}
        // z-50, above the feedback button's z-40: both live in the bottom-left
        // corner, and at equal depth the later-mounted one wins and covers this
        // panel's release/share controls.
        //
        // Frosted rather than solid. On the trip page this slides over the 3D
        // journey scene, and a flat white column there cuts the picture in two;
        // letting the scene blur through keeps the panel reading as something
        // laid ON the page rather than a hole punched in it. The cards inside
        // stay opaque, so the text they carry never sits on a moving image.
        className={`fixed left-0 top-0 z-50 flex h-dvh w-[min(22rem,90vw)] flex-col border-r border-ink-100/80 bg-white/80 shadow-xl backdrop-blur-xl transition-transform duration-200 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Safe areas top and bottom: this panel is full-height on a phone, so
            without them the title sits under the notch and the release control
            sits under the home indicator. `FeedbackLink` does the same. */}
        <header className="flex items-center justify-between border-b border-ink-100/80 px-4 py-3 pt-[max(0.75rem,env(safe-area-inset-top))]">
          <h2 className="font-display text-base font-semibold text-ink-900">
            Your trips
          </h2>
          <button
            type="button"
            onClick={() => toggle(false)}
            aria-label="Close your trips"
            className="rounded-lg p-1.5 text-ink-400 transition hover:bg-ink-50 hover:text-ink-700"
          >
            <PanelLeftClose className="h-5 w-5" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {username === null ? (
            <ClaimForm
              onClaimed={(name) => {
                setUsername(name)
                setStoredUsername(name)
              }}
            />
          ) : loading && trips === null ? (
            <p className="flex items-center gap-2 text-sm text-ink-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading your trips…
            </p>
          ) : trips && trips.length > 0 ? (
            <ul className="grid gap-3">
              {trips.map((t) => (
                <TripSummaryCard key={t.id} trip={t} />
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
          <DrawerFooter
            username={username}
            onReleased={() => {
              setUsername(null)
              setTrips(null)
              forgetUsername()
            }}
            onAdopted={(name) => {
              setUsername(name)
              setStoredUsername(name)
              setTrips(null)
            }}
          />
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
        placeholder="marc"
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
      if (!adoptSecret(value)) {
        setError("That does not look like a device code.")
        return
      }
      const profile = await whoAmI()
      if (!profile) {
        setError("That code works, but it holds no username yet.")
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
