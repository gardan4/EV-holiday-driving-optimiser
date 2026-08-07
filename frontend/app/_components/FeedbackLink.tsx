"use client"

/**
 * Feedback, in place.
 *
 * The obvious move was a link to a Google Form. Two things against it: a
 * visitor who has to leave the site and load someone else's page mostly
 * doesn't, which is the whole ballgame for response rate — and the privacy
 * notice now says no third party is involved, which would stop being true.
 *
 * So it posts to our own API. The second thing costing responses was where it
 * lived: an underlined word in the footer, between "Privacy" and "Source",
 * looking exactly like navigation and sitting below a page most people never
 * scroll to the end of. Now there is a floating button that is visible without
 * scrolling anywhere, and it opens a real panel — a sheet on a phone, a card
 * above the button on a desktop — instead of unfolding inside the footer row.
 *
 * Still not a modal: Escape or a click outside closes it, nothing traps focus.
 * Mounted from `SiteFooter`, which keeps it off the full-bleed live and drive
 * screens where a floating button would be furniture in the driver's way.
 */

import { useEffect, useRef, useState } from "react"
import { usePathname } from "next/navigation"
import { Check, Loader2, MessageSquarePlus, Send, X } from "lucide-react"
import { sendFeedback } from "@/lib/client"

const MAX = 2000

/** Openers for the blank box — the hardest part of feedback is starting it. */
const STARTERS = [
  "My car is missing",
  "The numbers look wrong",
  "Something broke",
  "An idea",
]

export default function FeedbackLink() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState("")
  const [contact, setContact] = useState("")
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle")
  const [error, setError] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // Whichever of the two buttons opened it, so Escape hands focus back there
  // instead of dropping it on the body.
  const openerRef = useRef<HTMLButtonElement | null>(null)

  // Escape and click-away close it. Both are what a panel that dims nothing and
  // steals no focus has to honour to feel dismissible rather than stuck.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false)
        openerRef.current?.focus()
      }
    }
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement
      // The trigger toggles on click; closing it here first would reopen it.
      if (panelRef.current?.contains(t) || t.closest("[data-feedback-trigger]")) return
      setOpen(false)
    }
    document.addEventListener("keydown", onKey)
    document.addEventListener("mousedown", onDown)
    return () => {
      document.removeEventListener("keydown", onKey)
      document.removeEventListener("mousedown", onDown)
    }
  }, [open])

  // Focusing the box on open is right on a desktop: the panel is small, and
  // the pointer is already there. On a phone it throws the keyboard up over
  // the starter chips before you have read them — and the chips are the part
  // that gets people writing — so there the box waits to be tapped.
  useEffect(() => {
    if (!open) return
    if (window.matchMedia("(min-width: 640px)").matches) textareaRef.current?.focus()
  }, [open])

  function start(text: string) {
    setMessage(`${text}: `)
    const el = textareaRef.current
    if (el) {
      el.focus()
      // Cursor after the colon, not before it.
      requestAnimationFrame(() => el.setSelectionRange(el.value.length, el.value.length))
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!message.trim() || state === "sending") return
    setState("sending")
    setError(null)
    try {
      await sendFeedback({ message, contact: contact || null, path: pathname })
      setState("sent")
      setMessage("")
      setContact("")
    } catch (err) {
      setState("error")
      setError(err instanceof Error ? err.message : "Could not send that")
    }
  }

  function close() {
    setOpen(false)
    if (state === "sent") setState("idle")
    openerRef.current?.focus()
  }

  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    openerRef.current = e.currentTarget
    setOpen((o) => !o)
  }

  const remaining = MAX - message.length

  return (
    <>
      {/* In the footer, where someone reading the small print looks for it.
          A filled chip rather than another underlined word: the two things
          beside it navigate, this one does something. */}
      <button
        type="button"
        data-feedback-trigger
        onClick={toggle}
        aria-expanded={open}
        aria-controls="feedback-panel"
        className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-3 py-1 font-medium text-brand-700 transition-colors hover:border-brand-400 hover:bg-brand-100"
      >
        <MessageSquarePlus className="h-3.5 w-3.5" />
        Send feedback
      </button>

      {/* And again as a floating button, because the footer is the one part of
          a long results page nobody scrolls to. Bottom-LEFT on purpose: the
          toasts live bottom-right. */}
      <button
        type="button"
        data-feedback-trigger
        onClick={toggle}
        aria-expanded={open}
        aria-controls="feedback-panel"
        aria-hidden={open}
        tabIndex={open ? -1 : 0}
        // Only the hover lift transitions. Opacity deliberately does not: the
        // panel opens exactly where this button sits, so it has to be gone the
        // moment the panel appears, not 150ms later.
        className={`fixed bottom-4 left-4 z-40 inline-flex items-center gap-2 rounded-full border border-ink-200 bg-white/95 px-4 py-2.5 text-sm font-semibold text-ink-700 shadow-lg shadow-ink-900/10 backdrop-blur transition-[transform,box-shadow,color,border-color] duration-150 hover:-translate-y-0.5 hover:border-brand-300 hover:text-brand-700 hover:shadow-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 ${
          open ? "pointer-events-none opacity-0" : "opacity-100"
        }`}
      >
        <MessageSquarePlus className="h-4 w-4 text-brand-600" />
        Feedback
      </button>

      {open && (
        <>
          {/* Phone only: the sheet covers the page anyway, so say so. */}
          <div
            className="fixed inset-0 z-40 bg-ink-900/20 sm:hidden"
            onClick={close}
            aria-hidden
          />
          <div
            ref={panelRef}
            id="feedback-panel"
            role="dialog"
            aria-modal="false"
            aria-labelledby="feedback-title"
            // `max-h` + scroll so the sheet can never grow past the screen
            // once the keyboard has taken the bottom half, and the extra
            // bottom padding clears the home indicator on a notched phone.
            className="fixed inset-x-0 bottom-0 z-50 max-h-[85dvh] overflow-y-auto rounded-t-2xl border border-ink-200 bg-white p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] text-left shadow-2xl shadow-ink-900/20 sm:inset-x-auto sm:bottom-4 sm:left-4 sm:w-[24rem] sm:rounded-2xl sm:pb-4"
          >
            {state === "sent" ? (
              <div className="py-2 text-center">
                <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-brand-50">
                  <Check className="h-5 w-5 text-brand-600" />
                </div>
                <p className="mt-3 font-display text-base font-semibold text-ink-900">
                  That landed — thank you.
                </p>
                <p className="mt-1 text-sm text-ink-500">
                  It genuinely helps. Every message gets read.
                </p>
                <button
                  type="button"
                  onClick={close}
                  className="mt-4 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm font-semibold text-ink-700 hover:bg-ink-100"
                >
                  Close
                </button>
              </div>
            ) : (
              <form onSubmit={submit}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2
                      id="feedback-title"
                      className="font-display text-base font-semibold text-ink-900"
                    >
                      What would make this better?
                    </h2>
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
                      Goes straight to the person who built it — no account, no
                      third party.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={close}
                    aria-label="Close feedback"
                    className="-mr-1 -mt-1 shrink-0 rounded-lg p-1.5 text-ink-400 hover:bg-ink-100 hover:text-ink-700"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                {message.length === 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {STARTERS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => start(s)}
                        className="rounded-full border border-ink-200 px-2.5 py-1 text-xs text-ink-600 transition-colors hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}

                {/* 16px on a phone, 14 from `sm` up. Under 16, WebKit zooms
                    the page the instant the field takes focus — which, with a
                    panel that focuses on open, meant tapping Feedback zoomed
                    you in and left the sheet sitting off-centre. */}
                <textarea
                  id="feedback-message"
                  ref={textareaRef}
                  value={message}
                  onChange={(e) => setMessage(e.target.value.slice(0, MAX))}
                  rows={4}
                  aria-label="Your feedback"
                  className="mt-3 w-full resize-y rounded-lg border border-ink-200 px-3 py-2 text-base text-ink-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-200 sm:text-sm"
                  placeholder="The charging curve for my car looks off…"
                />
                {remaining < 200 && (
                  <p className="mt-1 text-right text-xs text-ink-400">
                    {remaining} characters left
                  </p>
                )}

                <input
                  type="email"
                  value={contact}
                  onChange={(e) => setContact(e.target.value.slice(0, 200))}
                  autoComplete="email"
                  aria-label="Your email, only if you want a reply"
                  className="mt-2 w-full rounded-lg border border-ink-200 px-3 py-2 text-base text-ink-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-200 sm:text-sm"
                  placeholder="Email — only if you want a reply (optional)"
                />

                <button
                  type="submit"
                  disabled={!message.trim() || state === "sending"}
                  className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-3.5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-ink-200 disabled:text-ink-400"
                >
                  {state === "sending" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                  {state === "sending" ? "Sending…" : "Send feedback"}
                </button>
                {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
              </form>
            )}
          </div>
        </>
      )}
    </>
  )
}
