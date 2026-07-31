import Link from "next/link"

/**
 * Public landing page. Replace this with your real marketing page — it's the one
 * route the middleware leaves fully public and indexable.
 */
export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-[#f8fafc] text-slate-800">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-32 -left-32 h-96 w-96 rounded-full bg-brand-300/30 blur-3xl animate-float" />
        <div className="absolute -bottom-32 right-0 h-80 w-80 rounded-full bg-sky-300/25 blur-3xl animate-float" />
      </div>

      <section className="relative z-10 mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center px-6 text-center">
        <p className="font-mono text-xs uppercase tracking-[0.22em] text-brand-500">
          Full-stack starter
        </p>
        <h1 className="mt-4 text-5xl font-bold tracking-tight text-slate-900 sm:text-6xl">
          EV Trip Optimizer
        </h1>
        <p className="mt-5 max-w-xl text-lg leading-relaxed text-slate-500">
          A production-shaped skeleton: FastAPI + async SQLAlchemy on Azure SQL,
          Next.js 16 with Clerk auth, Docker, Bicep, and CI. Sign in to see the
          multi-tenant dashboard, then build your product on top.
        </p>
        <div className="mt-9 flex items-center gap-3">
          <Link
            href="/dashboard"
            className="rounded-xl bg-brand-500 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-600"
          >
            Open the app
          </Link>
          <Link
            href="/register"
            className="rounded-xl border border-slate-200 bg-white px-6 py-3 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100"
          >
            Create an account
          </Link>
        </div>
      </section>
    </main>
  )
}
