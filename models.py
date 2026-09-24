"""The database schema.

Two conventions worth stating, because both are load-bearing elsewhere:

    Money columns are Numeric, not Float. Filed financials are exact decimal
    quantities and binary floating point cannot represent them exactly.
    Numeric hands back Decimal, which is why serialization.to_jsonable exists
    to convert at the boundaries.

    Every financial column is nullable, deliberately. A company that did not
    file a figure has no figure, and the pipeline carries that absence all the
    way through rather than substituting zero.
"""

from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    period_end: Mapped[date]
    revenue: Mapped[float | None] = mapped_column(Numeric)
    net_income: Mapped[float | None] = mapped_column(Numeric)
    free_cash_flow: Mapped[float | None] = mapped_column(Numeric)
    total_debt: Mapped[float | None] = mapped_column(Numeric)
    shareholders_equity: Mapped[float | None] = mapped_column(Numeric)
    cash: Mapped[float | None] = mapped_column(Numeric)
    short_term_investments: Mapped[float | None] = mapped_column(Numeric)

    company = relationship("Company", back_populates="financials")


class Brief(Base):
    """A cached answer to one question about one company's filing.

    The unique constraint over (company_id, question) is also the lookup
    index, and question is user-supplied - which is why main.py caps its
    length. Postgres limits a btree entry to 2704 bytes, and an unbounded
    question that does not compress exceeds it and raises on insert.
    """

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
    # The report date of the filing this answer was derived from. Read back to
    # decide whether a newer 10-K has superseded the cached brief.
    report_date: Mapped[str]
    # timezone=True, matching reports.generated_at. As a naive column this
    # stored UTC by convention and said so nowhere, leaving every reader to
    # assume - and the API emitted it with no offset at all.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("company_id", name="uq_report_company"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    payload: Mapped[str] = mapped_column(Text)  # the full report as JSON
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Deferred: vector retrieval for free-form Q&A. The sketch lives in
# chunking.py and embeddings.py; the model would add a chunks table with a
# Vector(384) embedding column and a unique (company_id, chunk_index). It
# needs `pip install sentence-transformers pgvector`, which requirements.txt
# no longer installs - see the note there.
