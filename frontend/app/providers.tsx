"use client"

import { Toaster } from "sonner"
import Analytics from "./_components/Analytics"
import TripsDrawer from "./_components/trips/TripsDrawer"
import { TripsProvider } from "./_components/trips/TripsContext"

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <TripsProvider>
      <Analytics />
      {children}
      {/* Mounted here rather than per page, so the header button has a panel to
          open wherever the header is. It renders nothing until it is opened. */}
      <TripsDrawer />
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "#0f172a",
            border: "1px solid #1e293b",
            color: "#e2e8f0",
          },
          classNames: {
            // Sonner's action chip is 24 px tall. Several of these are pressed
            // one-handed in a moving car — "Re-plan" after a charge stop,
            // "Battery?" after a re-route — and 24 px is half what a thumb
            // needs. Set once here rather than per toast, so no call site can
            // ship a target the others already grew out of.
            actionButton: "min-h-11 px-3 text-sm font-semibold",
            cancelButton: "min-h-11 px-3 text-sm",
          },
        }}
      />
    </TripsProvider>
  )
}
