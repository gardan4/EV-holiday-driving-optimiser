"""Database models for EV Trip Optimizer.

Tenancy spine: User → Business (strict tenant boundary) → Item (example child
resource). `Item` is the canonical "copy me" resource — duplicate it (model +
`api/items.py` router + a migration + a frontend tab) to add your own
tenant-scoped entity. See docs/ADDING_A_RESOURCE.md.

MSSQL note: UUID primary keys use the `GUID` TypeDecorator (CHAR(36)); a plain
`uuid` column type has no portable MSSQL mapping. Keep it for every id/FK.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    TypeDecorator,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class GUID(TypeDecorator):
    """Platform-independent GUID type. CHAR(36) for MSSQL compatibility."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# ---------------------------------------------------------------------------
# Identity & tenancy
# ---------------------------------------------------------------------------


class User(Base):
    """Global user identity. Authentication is owned by Clerk; this is the local
    mirror, JIT-provisioned on first sign-in (see app/api/auth.py)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(255), nullable=False)

    # External identity-provider key (Clerk owns authentication). Nullable so an
    # erased/tombstoned row can hold NULL. Uniqueness is enforced by a FILTERED
    # index in __table_args__ — a plain UNIQUE on a nullable column only permits
    # one NULL row on SQL Server, so the filter scopes uniqueness to non-NULL.
    clerk_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Mirror of Clerk's email-verified state (kept locally for /me + gating).
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )

    __table_args__ = (
        Index(
            "uq_users_clerk_user_id",
            "clerk_user_id",
            unique=True,
            mssql_where=text("clerk_user_id IS NOT NULL"),
        ),
    )

    # Onboarding gate (example lifecycle flag). Flip via POST /api/auth/onboarded.
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    # Legal consent — stamped at first provisioning (configure legal links in Clerk).
    tos_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Local ban switch (deactivate without touching Clerk). Also set by erasure.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owned_businesses: Mapped[List["Business"]] = relationship(
        "Business",
        back_populates="owner",
        foreign_keys="Business.owner_user_id",
        cascade="all, delete-orphan",
    )
    business_memberships: Mapped[List["BusinessMembership"]] = relationship(
        "BusinessMembership", back_populates="user", cascade="all, delete-orphan"
    )


class Business(Base):
    """A business / organization. Strict tenant boundary — zero cross-business
    sharing. A user can own many businesses."""

    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Soft-delete: archived businesses are hidden everywhere (deps.py 404s them).
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    owner: Mapped["User"] = relationship(
        "User", back_populates="owned_businesses", foreign_keys=[owner_user_id]
    )
    memberships: Mapped[List["BusinessMembership"]] = relationship(
        "BusinessMembership", back_populates="business", cascade="all, delete-orphan"
    )
    items: Mapped[List["Item"]] = relationship(
        "Item", back_populates="business", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "archived_at": self.archived_at.isoformat() + "Z" if self.archived_at else None,
        }


class BusinessMembership(Base):
    """A user's membership in a business with a role.

    `owner` is derived from Business.owner_user_id and always has full access;
    additional members can be promoted to `owner` for the same rights. `member`
    is the default for invited users.
    """

    __tablename__ = "business_memberships"
    __table_args__ = (UniqueConstraint("business_id", "user_id", name="uq_business_membership"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("businesses.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    business: Mapped["Business"] = relationship("Business", back_populates="memberships")
    user: Mapped["User"] = relationship("User", back_populates="business_memberships")


# ---------------------------------------------------------------------------
# Example tenant-scoped resource — copy this to build your own
# ---------------------------------------------------------------------------


class Item(Base):
    """Example child resource, scoped to a Business (the tenant).

    This is the reference CRUD entity the skeleton ships. To add your own
    resource, copy this model, add an `api/<resource>.py` router mounted the
    same way `items` is, generate a migration, and add a frontend tab.
    """

    __tablename__ = "items"
    __table_args__ = (Index("ix_items_business", "business_id", "archived_at"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("businesses.id"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(UnicodeText, nullable=True)
    # Free-form status label ("active" | "archived" | your own vocab). A string,
    # not a bool — MSSQL `IS 1` boolean filters are a T-SQL syntax error.
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="items")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "data": self.data,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
            "archived_at": self.archived_at.isoformat() + "Z" if self.archived_at else None,
        }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class UserAuditLog(Base):
    """Audit log of user-initiated writes. Written via api/deps.audit()."""

    __tablename__ = "user_audit_logs"
    __table_args__ = (
        Index("ix_user_audit_business_time", "business_id", "created_at"),
        Index("ix_user_audit_user_time", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("businesses.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
