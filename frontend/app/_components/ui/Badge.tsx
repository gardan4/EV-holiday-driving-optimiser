"use client"

import { ReactNode } from "react"

type Tone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "muted"
  | "brand"

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-100/80 text-slate-700 border border-slate-200/60",
  info: "bg-blue-100/80 text-blue-700 border border-blue-200/60",
  success: "bg-emerald-100/80 text-emerald-700 border border-emerald-200/60",
  warning: "bg-amber-100/80 text-amber-700 border border-amber-200/60",
  danger: "bg-red-100/80 text-red-700 border border-red-200/60",
  muted: "bg-white/60 text-slate-500 border border-slate-200/60 backdrop-blur-sm",
  brand: "bg-brand-100/70 text-brand-700 border border-brand-200/60",
}

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode
  tone?: Tone
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]} ${className}`}
    >
      {children}
    </span>
  )
}

/** Map a status string to a badge tone. */
export function statusTone(status: string): Tone {
  if (status === "running") return "success"
  if (status === "planning") return "warning"
  if (status === "complete" || status === "completed") return "info"
  if (status === "failed") return "danger"
  if (status === "interrupted" || status === "paused") return "warning"
  return "neutral"
}
