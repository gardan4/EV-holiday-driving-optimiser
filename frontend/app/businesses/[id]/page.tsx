"use client"

import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import AppHeader from "../../_components/AppHeader"
import { Badge, Button, Card, Input } from "../../_components/ui"
import {
  archiveItem,
  createItem,
  getBusiness,
  listItems,
  listMembers,
  setMemberRole,
  type Business,
  type Item,
  type Member,
} from "@/lib/client"

type Tab = "items" | "members"

export default function BusinessDetailPage() {
  const params = useParams<{ id: string }>()
  const businessId = params.id

  const [business, setBusiness] = useState<Business | null>(null)
  const [tab, setTab] = useState<Tab>("items")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getBusiness(businessId)
      .then(setBusiness)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
  }, [businessId])

  return (
    <div className="min-h-screen bg-[#f8fafc]">
      <AppHeader subtitle={business?.name} />
      <main className="mx-auto max-w-6xl px-6 py-8">
        <h1 className="text-2xl font-bold text-slate-900">{business?.name ?? "…"}</h1>

        <div className="my-6 flex gap-2 border-b border-slate-200">
          {(["items", "members"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-semibold capitalize transition-colors ${
                tab === t
                  ? "border-b-2 border-brand-500 text-brand-600"
                  : "text-slate-500 hover:text-brand-500"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {error && (
          <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        {tab === "items" ? (
          <ItemsTab businessId={businessId} />
        ) : (
          <MembersTab businessId={businessId} />
        )}
      </main>
    </div>
  )
}

function ItemsTab({ businessId }: { businessId: string }) {
  const [items, setItems] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    try {
      setItems(await listItems(businessId))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId])

  async function onCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    try {
      await createItem(businessId, { name: name.trim(), description: description.trim() || undefined })
      setName("")
      setDescription("")
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create")
    } finally {
      setBusy(false)
    }
  }

  async function onArchive(id: string) {
    try {
      await archiveItem(businessId, id)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to archive")
    }
  }

  return (
    <div className="space-y-6">
      <Card title="New item">
        <form onSubmit={onCreate} className="space-y-3">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Widget" />
          <Input
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
          />
          <Button type="submit" disabled={busy || !name.trim()}>
            {busy ? "Adding…" : "Add item"}
          </Button>
        </form>
      </Card>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      {loading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-400">No items yet.</p>
      ) : (
        <div className="space-y-2">
          {items.map((i) => (
            <Card key={i.id} padded={false}>
              <div className="flex items-center justify-between gap-4 p-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-800">{i.name}</span>
                    <Badge tone="brand">{i.status}</Badge>
                  </div>
                  {i.description && <p className="mt-0.5 truncate text-sm text-slate-500">{i.description}</p>}
                </div>
                <Button variant="danger" size="sm" onClick={() => onArchive(i.id)}>
                  Archive
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

function MembersTab({ businessId }: { businessId: string }) {
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    try {
      setMembers(await listMembers(businessId))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId])

  async function toggleRole(m: Member) {
    try {
      await setMemberRole(businessId, m.user_id, m.role === "owner" ? "member" : "owner")
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update role")
    }
  }

  if (loading) return <p className="text-sm text-slate-400">Loading…</p>

  return (
    <div className="space-y-3">
      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}
      {members.map((m) => (
        <Card key={m.user_id} padded={false}>
          <div className="flex items-center justify-between gap-4 p-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-slate-800">{m.name}</span>
                <Badge tone={m.role === "owner" ? "success" : "neutral"}>{m.role}</Badge>
                {m.is_founder && <Badge tone="muted">founder</Badge>}
              </div>
              <p className="truncate text-sm text-slate-500">{m.email}</p>
            </div>
            {!m.is_founder && (
              <Button variant="secondary" size="sm" onClick={() => toggleRole(m)}>
                Make {m.role === "owner" ? "member" : "owner"}
              </Button>
            )}
          </div>
        </Card>
      ))}
    </div>
  )
}
