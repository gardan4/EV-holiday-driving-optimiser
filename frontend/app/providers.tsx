"use client"

import { Toaster } from "sonner"
import Analytics from "./_components/Analytics"

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Analytics />
      {children}
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "#0f172a",
            border: "1px solid #1e293b",
            color: "#e2e8f0",
          },
        }}
      />
    </>
  )
}
