/**
 * Public landing page — the planner form lands here (M5). Placeholder copy for
 * now so the auth-free app boots cleanly with the real product story.
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
          EV road-trip speed optimizer
        </p>
        <h1 className="mt-4 text-5xl font-bold tracking-tight text-slate-900 sm:text-6xl">
          Should you really drive 100?
        </h1>
        <p className="mt-5 max-w-xl text-lg leading-relaxed text-slate-500">
          Enter your route and your EV, and find the cruise speed that actually
          gets you there first — charging stops, taper curves and all. Then race
          the result against your friend&apos;s plan.
        </p>
        <p className="mt-9 rounded-xl border border-slate-200 bg-white px-6 py-3 text-sm font-semibold text-slate-400">
          Trip planner coming up — under construction
        </p>
      </section>
    </main>
  )
}
