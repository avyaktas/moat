from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from datetime import date, datetime
from sqlalchemy import ForeignKey, Numeric, UniqueConstraint, DateTime, Text, func
from pgvector.sqlalchemy import Vector

class Base(DeclarativeBase):
    pass

class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    sector: Mapped[str | None]

    financials = relationship("Financials", back_populates="company")


class Financials(Base):
    __tablename__ = "financials"
    __table_args__ = (
        UniqueConstraint("company_id", "period_end", name="uq_company_period"),
        )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies." \
    "id"))
    period_end: Mapped[date]
    revenue: Mapped[float | None] = mapped_column(Numeric)
    net_income: Mapped[float | None] = mapped_column(Numeric)
    free_cash_flow: Mapped[float | None] = mapped_column(Numeric)
    total_debt: Mapped[float | None] = mapped_column(Numeric)
    shareholders_equity: Mapped[float | None] = mapped_column(Numeric)

    company = relationship("Company", back_populates="financials")

from datetime import datetime
from sqlalchemy import DateTime, Text, func


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        UniqueConstraint("company_id", "question", name="uq_company_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)        
    addressed: Mapped[bool]
    quotes: Mapped[str] = mapped_column(Text)        
    grounding_rate: Mapped[float | None]
    filing_url: Mapped[str] = mapped_column(Text)
    report_date: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# Deferred: vector retrieval for free form Q and A#
#class Chunk(Base):
   # __tablename__ = "chunks"
    #__table_args__ = (
    #    UniqueConstraint("company_id", "chunk_index", name="uq_company_chunk"),
    #)

    #id: Mapped[int] = mapped_column(primary_key=True)
    #company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    #chunk_index: Mapped[int]
    #text: Mapped[str] = mapped_column(Text)
    #embedding: Mapped[list[float]] = mapped_column(Vector(384))
    #filing_url: Mapped[str] = mapped_column(Text)
    #report_date: Mapped[str]