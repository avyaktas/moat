from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from datetime import date
from sqlalchemy import ForeignKey, Numeric, UniqueConstraint

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



