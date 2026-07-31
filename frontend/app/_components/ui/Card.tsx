"use client"

import { ReactNode } from "react"

interface CardProps {
  title?: string
  action?: ReactNode
  children: ReactNode
  padded?: boolean
  className?: string
}

export function Card({
  title,
  action,
  children,
  padded = true,
  className = "",
}: CardProps) {
  return (
    <div
      className={`relative rounded-2xl border border-white/60 bg-white/55 backdrop-blur-[40px] backdrop-saturate-[150%] shadow-[0_2px_12px_rgba(0,0,0,0.05),inset_0_1px_1px_rgba(255,255,255,0.85)] overflow-hidden ${className}`}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/80 to-transparent" />
      {(title || action) && (
        <header className="px-5 py-3 border-b border-slate-100/80 flex items-center justify-between gap-3 relative">
          {title && (
            <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          )}
          {action}
        </header>
      )}
      <div className={padded ? "p-5 relative" : "relative"}>{children}</div>
    </div>
  )
}
