"use client"

/**
 * Feedback, in place.
 *
 * The obvious move was a link to a Google Form. Two things against it: a
 * visitor who has to leave the site and load someone else's page mostly
 * doesn't, which is the whole ballgame for response rate — and the privacy
 * notice now says no third party is involved, which would stop being true.
 *
 * So it posts to our own API. Opens under the footer rather than as a modal:
 * nothing is blocked, nothing traps focus, and closing it is the same click
 * that opened it.
 */

import { useState } from "react"
import { usePathname } from "next/navigation"
import { Check, Loader2, MessageSquare } from "lucide-react"
import { sendFeedback } from "@/lib/client"

const MAX = 2000

export default function FeedbackLink() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState("")
  const [contact, setContact] = useState("")
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle")
  const [error, setError] = useState<string | null>(null)

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

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 underline underline-offset-2 hover:text-ink-800"
      >
        <MessageSquare className="h-3.5 w-3.5" />
        Feedback
      </button>
    )
  }

  return (
    <div className="w-full">
      {state === "sent" ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3">
          <Check className="h-4 w-4 shrink-0 text-brand-600" />
          <span className="text-sm text-ink-700">
            Thank you — that landed. It genuinely helps.
          </span>
          <button
            onClick={() => {
              setOpen(false)
              setState("idle")
            }}
            className="ml-auto text-sm text-ink-500 underline underline-offset-2"
          >
            Close
          </button>
        </div>
      ) : (
        <form
          onSubmit={submit}
          className="rounded-xl border border-ink-200 bg-white p-4"
        >
          <label
            htmlFor="feedback-message"
            className="font-display text-sm font-semibold text-ink-900"
          >
            What would make this better?
          </label>
          <p className="mt-0.5 text-xs text-ink-500">
            Wrong numbers, a car that&apos;s missing, something that broke — all
            useful.
          </p>
          <textarea
            id="feedback-message"
            value={message}
            onChange={(e) => setMessage(e.target.value.slice(0, MAX))}
            rows={4}
            autoFocus
            className="mt-2 w-full resize-y rounded-lg border border-ink-200 px-3 py-2 text-sm text-ink-900 outline-none focus:border-brand-400"
            placeholder="The charging curve for my car looks off…"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={contact}
              onChange={(e) => setContact(e.target.value.slice(0, 200))}
              className="min-w-0 flex-1 rounded-lg border border-ink-200 px-3 py-2 text-sm text-ink-900 outline-none focus:border-brand-400"
              placeholder="Email, only if you want a reply (optional)"
            />
            <button
              type="submit"
              disabled={!message.trim() || state === "sending"}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700 disabled:opacity-50"
            >
              {state === "sending" && <Loader2 className="h-4 w-4 animate-spin" />}
              Send
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="px-2 py-2 text-sm text-ink-500 underline underline-offset-2"
            >
              Cancel
            </button>
          </div>
          {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
        </form>
      )}
    </div>
  )
}
