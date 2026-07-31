# Adding a tenant-scoped resource

The skeleton ships one example child resource, **`Item`**, scoped to a `Business`.
To add your own (say, `Project`), copy the five pieces of the `Item` slice.

## 1. Model — `src/app/models/__init__.py`

Copy the `Item` class. Keep the `business_id` FK + `GUID` ids, and prefer a
string `status` over a boolean (MSSQL `IS 1` boolean filters are a syntax error).

```python
class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_business", "business_id", "archived_at"),)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("businesses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    # ... created_at / updated_at / archived_at, and a to_dict()
```

Add the reverse relationship on `Business` (`projects: Mapped[List["Project"]]`).

## 2. Dependency — `src/app/api/deps.py`

Copy `require_item` → `require_project`: resolve the business via
`require_business_member`, then look the child up scoped to `business.id`. This is
what enforces tenancy — never query a child by id alone.

## 3. Router — `src/app/api/projects.py`

Copy `api/items.py`. It's a self-contained `APIRouter` with list/create/get/
patch/delete, each gated by `require_business_member` / `require_project` and
writing an `audit()` row.

## 4. Mount it — `src/app/main.py`

```python
from app.api import projects
...
app.include_router(
    projects.router,
    prefix="/api/businesses/{business_id}/projects",
    tags=["projects"],
)
```

## 5. Migration

```bash
cd src && uv run alembic revision --autogenerate -m "add projects"
# review the generated file, then:
uv run alembic upgrade head
```

## 6. Frontend — `frontend/lib/client.ts` + a UI tab

Copy the `items` block in `lib/client.ts` (types + `listProjects` /
`createProject` / …), then add a tab to `frontend/app/businesses/[id]/page.tsx`
modeled on `ItemsTab`.

## Checklist

- [ ] Model + reverse relationship + `to_dict()`
- [ ] `require_<resource>` dependency
- [ ] Router with `audit()` on every write
- [ ] Router mounted in `main.py`
- [ ] Migration generated, reviewed, applied
- [ ] `lib/client.ts` wrappers + UI tab
- [ ] `npx tsc --noEmit` (frontend) and the backend boots
