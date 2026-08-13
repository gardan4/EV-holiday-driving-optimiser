import { useState, type FormEvent } from "react"
import { api } from "../lib/api"

/** The sign-in screen.
 *
 * The password is `STATS_TOKEN` and it is exchanged for an httpOnly cookie, so
 * this is the only moment it exists in the browser at all — after this, no
 * script on the page can read the credential back out.
 */
export function Login({ onIn }: { onIn: () => void }) {
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (await api.login(password)) {
        onIn()
      } else {
        setError("That is not the token.")
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.")
    } finally {
      setBusy(false)
      setPassword("")
    }
  }

  return (
    <main className="relative z-10 grid min-h-full place-items-center px-6">
      <form onSubmit={submit} className="panel w-full max-w-sm">
        <h1 className="display text-[18px] font-semibold">EV Trip mission control</h1>
        <p className="mt-1 text-[12.5px] text-ink-mute">
          Sign in with the stats token.
        </p>

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          autoComplete="current-password"
          placeholder="token"
          className="mt-4 w-full rounded-lg border border-deck-line bg-deck-raised px-3 py-2 text-[14px] outline-none placeholder:text-ink-mute focus:border-s-mint"
        />

        {error && (
          <p className="mt-2 text-[12.5px]" style={{ color: "var(--color-s-rose)" }}>
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="mt-4 w-full rounded-lg px-3 py-2 text-[13px] font-medium transition-opacity disabled:opacity-40"
          style={{ background: "var(--color-s-mint)", color: "#06231a" }}
        >
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  )
}
