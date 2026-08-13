import Link from "next/link"

export default function NotFound() {
  return (
    <main className="min-h-screen flex items-center justify-center bg-slate-50 px-6">
      <div className="text-center max-w-md">
        <p className="font-mono text-sm uppercase tracking-[0.22em] text-brand-500">
          404
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-900">
          Page not found.
        </h1>
        <p className="mt-3 text-slate-500 leading-relaxed">
          The page you&rsquo;re after doesn&rsquo;t exist or has moved.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link
            href="/"
            className="px-5 py-2.5 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold transition-colors"
          >
            Back to home
          </Link>
          <Link
            href="/ev"
            className="px-5 py-2.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-100 text-slate-700 text-sm font-semibold transition-colors"
          >
            Browse the EV catalog
          </Link>
        </div>
      </div>
    </main>
  )
}
