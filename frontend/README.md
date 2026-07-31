# EV Trip Optimizer — frontend

Next.js 16 (App Router, React 19) + Clerk auth + Tailwind CSS 4.

## Local development

```bash
npm install
npm run dev          # http://localhost:3000
```

Set these in `.env.local` (see the root `.env.example` for the full contract):

```
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
NEXT_PUBLIC_API_URL=http://localhost:8000   # the FastAPI backend
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

The app will not render without a Clerk publishable key — create a Clerk
application first (see the root `README.md`).

## Layout

- `app/` — App Router pages. `page.tsx` (public landing), `login/` + `register/`
  (Clerk), `dashboard/` (businesses list), `businesses/[id]/` (Items + Members).
- `app/_components/ui/` — shared primitives (Button, Card, Input, Badge).
- `lib/api.ts` — auth token plumbing + `fetchWithAuth`.
- `lib/client.ts` — typed API client. Copy the `items` block to add a resource.
- `proxy.ts` — Clerk middleware + CSP + security headers (single source of truth).

## Build / lint

```bash
npm run lint
npx tsc --noEmit
npx next build       # one-shot production compile
```
