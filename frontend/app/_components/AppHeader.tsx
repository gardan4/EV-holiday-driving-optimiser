"use client"

import Link from "next/link"
import { UserButton } from "@clerk/nextjs"
import { ReactNode } from "react"

interface AppHeaderProps {
  /** Page-specific primary action (e.g. "New business" on the dashboard). */
  action?: ReactNode
  /** Optional subtitle next to the wordmark (e.g. a business name). */
  subtitle?: string
}

/**
 * Single nav surface for the authed product. Wordmark → /dashboard, an optional
 * page action, and Clerk's <UserButton> (account menu + sign-out). Keep it
 * simple — extend as your product grows.
 */
export default function AppHeader({ action, subtitle }: AppHeaderProps) {
  return (
    <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
        <Link href="/dashboard" className="flex min-w-0 items-center gap-2">
          <span className="text-lg font-bold text-brand-500">EV Trip Optimizer</span>
          {subtitle && (
            <span className="truncate text-sm text-slate-400">
              <span className="mx-1 text-slate-300">/</span>
              {subtitle}
            </span>
          )}
        </Link>
        <div className="flex items-center gap-4">
          {action}
          <UserButton />
        </div>
      </div>
    </header>
  )
}
