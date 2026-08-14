"use client"

import { ButtonHTMLAttributes, forwardRef } from "react"

type Variant = "primary" | "secondary" | "ghost" | "danger"
type Size = "sm" | "md" | "lg"

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

const VARIANT_CLASSES: Record<Variant, string> = {
  primary:
    "bg-brand-500 text-white hover:bg-brand-600 shadow-md shadow-brand-500/15 disabled:bg-slate-300 disabled:shadow-none",
  secondary:
    "border border-slate-200 bg-white/70 text-slate-600 hover:border-brand-300 hover:text-brand-500 shadow-[0_1px_4px_rgba(0,0,0,0.04)] backdrop-blur-sm",
  ghost: "text-slate-500 hover:text-brand-500",
  danger:
    "border border-red-200 bg-white/70 text-red-600 hover:bg-red-50 hover:border-red-300 backdrop-blur-sm disabled:opacity-50",
}

const SIZE_CLASSES: Record<Size, string> = {
  sm: "text-xs px-2.5 py-1 rounded-lg",
  md: "text-sm px-3.5 py-1.5 rounded-xl",
  lg: "text-base px-5 py-2.5 rounded-xl",
}

/**
 * The press is answered on the way DOWN, not on the click.
 *
 * `:active` fires on pointer-down, so the 3% scale lands while the finger is
 * still on the control rather than after the handler has run — which is the
 * difference between a button that feels like it heard you and one that feels
 * like it is deciding. It is the only feedback some of these buttons have:
 * `primary` submits a form and navigates, so its own hover state is gone by
 * the time anything happens.
 *
 * The property list is spelled out rather than `all` — `all` also animates the
 * layout properties a variant or a caller's `className` might change, off the
 * GPU and at this same 150ms.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className = "", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={`inline-flex items-center justify-center gap-1.5 font-semibold transition-[transform,background-color,border-color,color,box-shadow,opacity] duration-150 ease-out active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100 ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} ${className}`}
      {...rest}
    />
  )
})
