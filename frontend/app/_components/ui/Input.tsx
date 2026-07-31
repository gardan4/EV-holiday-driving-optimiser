"use client"

import { InputHTMLAttributes, TextareaHTMLAttributes, forwardRef } from "react"

const FIELD =
  "w-full rounded-xl border border-white/60 bg-white/60 backdrop-blur-sm px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 shadow-[0_1px_4px_rgba(0,0,0,0.03)] focus:outline-none focus:border-brand-300 focus:ring-2 focus:ring-brand-300/40 disabled:bg-slate-50 disabled:text-slate-400 transition"

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  hint?: string
  error?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, className = "", ...rest },
  ref,
) {
  return (
    <label className="block">
      {label && (
        <span className="block text-sm font-medium text-slate-700 mb-1.5">
          {label}
        </span>
      )}
      <input ref={ref} className={`${FIELD} ${className}`} {...rest} />
      {hint && !error && (
        <p className="mt-1 text-xs text-slate-400">{hint}</p>
      )}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </label>
  )
})

interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  hint?: string
  error?: string
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  function TextArea(
    { label, hint, error, className = "", rows = 3, ...rest },
    ref,
  ) {
    return (
      <label className="block">
        {label && (
          <span className="block text-sm font-medium text-slate-700 mb-1.5">
            {label}
          </span>
        )}
        <textarea
          ref={ref}
          rows={rows}
          className={`${FIELD} ${className}`}
          {...rest}
        />
        {hint && !error && (
          <p className="mt-1 text-xs text-slate-400">{hint}</p>
        )}
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      </label>
    )
  },
)
