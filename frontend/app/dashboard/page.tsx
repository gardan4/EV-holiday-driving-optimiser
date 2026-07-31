"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import AppHeader from "../_components/AppHeader"
import { Button, Card, Input } from "../_components/ui"
import { createBusiness, listBusinesses, type Business } from "@/lib/client"

export default function DashboardPage() {
  const [businesses, setBusinesses] = useState<Business[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [creating, setCreating] = useState(false)

  async function refresh() {
    try {
      setBusinesses(await listBusinesses())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function onCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    try {
      await createBusiness(name.trim())
      setName("")
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create")
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#f8fafc]">
      <AppHeader />
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Your businesses</h1>
            <p className="text-sm text-slate-500">Each business is an isolated tenant.</p>
          </div>
        </div>

        <div className="mb-8">
          <Card title="Create a business">
            <form onSubmit={onCreate} className="flex items-end gap-3">
              <div className="flex-1">
                <Input
                  label="Name"
                  placeholder="Acme Inc."
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <Button type="submit" disabled={creating || !name.trim()}>
                {creating ? "Creating…" : "Create"}
              </Button>
            </form>
          </Card>
        </div>

        {error && (
          <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        {loading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : businesses.length === 0 ? (
          <p className="text-sm text-slate-400">No businesses yet — create your first one above.</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {businesses.map((b) => (
              <Link key={b.id} href={`/businesses/${b.id}`}>
                <Card className="transition-shadow hover:shadow-lg">
                  <h2 className="font-semibold text-slate-800">{b.name}</h2>
                  <p className="mt-1 text-xs text-slate-400">
                    Created {b.created_at ? new Date(b.created_at).toLocaleDateString() : "—"}
                  </p>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
