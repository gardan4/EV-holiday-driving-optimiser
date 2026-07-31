import Link from "next/link"
import { SignIn } from "@clerk/nextjs"

export default function LoginPage() {
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#f8fafc] text-slate-700">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-32 -top-32 h-96 w-96 rounded-full bg-brand-300/30 blur-3xl animate-float" />
        <div className="absolute -right-32 top-1/2 h-80 w-80 rounded-full bg-purple-300/20 blur-3xl animate-float" />
      </div>

      <main className="relative z-10 flex flex-1 flex-col items-center justify-center gap-8 p-6">
        <Link href="/" className="text-2xl font-bold text-brand-500">
          EV Trip Optimizer
        </Link>
        <SignIn
          appearance={{
            elements: {
              rootBox: "w-full flex justify-center",
              card: "shadow-[0_4px_24px_rgba(0,0,0,0.08)] border border-white/60",
            },
          }}
        />
      </main>
    </div>
  )
}
