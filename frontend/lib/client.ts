/**
 * Typed API client for the EV Trip Optimizer backend.
 *
 * Thin wrappers over `fetchWithAuth` that parse JSON and surface a readable
 * error. Extend this as you add resources — copy the `items` block for your own
 * nested resource. See docs/ADDING_A_RESOURCE.md.
 */
import { fetchWithAuth, getErrorMessage } from "./api"

export type UserProfile = {
  id: string
  email: string
  name: string
  email_verified: boolean
  onboarded: boolean
  created_at: string
  owned_business_count: number
}

export type Business = {
  id: string
  name: string
  created_at: string | null
  archived_at: string | null
}

export type Member = {
  user_id: string
  email: string
  name: string
  role: "owner" | "member"
  is_founder: boolean
}

export type Item = {
  id: string
  business_id: string
  name: string
  description: string | null
  status: string
  data: Record<string, unknown> | null
  created_at: string | null
  updated_at: string | null
  archived_at: string | null
}

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(getErrorMessage(body?.detail, `Request failed (${res.status})`))
  }
  return body as T
}

// ── Auth / profile ──────────────────────────────────────────────────────────
export const me = () => fetchWithAuth("/api/auth/me").then((r) => json<UserProfile>(r))
export const markOnboarded = () =>
  fetchWithAuth("/api/auth/onboarded", { method: "POST" }).then((r) => json<{ onboarded: boolean }>(r))

// ── Businesses (the tenant) ───────────────────────────────────────────────────
export const listBusinesses = () =>
  fetchWithAuth("/api/businesses").then((r) => json<Business[]>(r))
export const createBusiness = (name: string) =>
  fetchWithAuth("/api/businesses", { method: "POST", body: JSON.stringify({ name }) }).then((r) =>
    json<Business>(r)
  )
export const getBusiness = (id: string) =>
  fetchWithAuth(`/api/businesses/${id}`).then((r) => json<Business>(r))
export const renameBusiness = (id: string, name: string) =>
  fetchWithAuth(`/api/businesses/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }).then(
    (r) => json<Business>(r)
  )
export const archiveBusiness = (id: string) =>
  fetchWithAuth(`/api/businesses/${id}`, { method: "DELETE" }).then((r) => json<{ archived: boolean }>(r))
export const listMembers = (businessId: string) =>
  fetchWithAuth(`/api/businesses/${businessId}/members`).then((r) => json<Member[]>(r))
export const setMemberRole = (businessId: string, userId: string, role: "owner" | "member") =>
  fetchWithAuth(`/api/businesses/${businessId}/members/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  }).then((r) => json<{ user_id: string; role: string }>(r))

// ── Items (the example nested resource — copy this block) ──────────────────────
export const listItems = (businessId: string) =>
  fetchWithAuth(`/api/businesses/${businessId}/items`).then((r) => json<Item[]>(r))
export const createItem = (
  businessId: string,
  payload: { name: string; description?: string; status?: string }
) =>
  fetchWithAuth(`/api/businesses/${businessId}/items`, {
    method: "POST",
    body: JSON.stringify(payload),
  }).then((r) => json<Item>(r))
export const updateItem = (
  businessId: string,
  itemId: string,
  payload: Partial<Pick<Item, "name" | "description" | "status">>
) =>
  fetchWithAuth(`/api/businesses/${businessId}/items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  }).then((r) => json<Item>(r))
export const archiveItem = (businessId: string, itemId: string) =>
  fetchWithAuth(`/api/businesses/${businessId}/items/${itemId}`, { method: "DELETE" }).then((r) =>
    json<{ archived: boolean }>(r)
  )
