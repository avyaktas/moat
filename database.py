"""How Python and Postgres are connected.

WHY THE POOL IS CONFIGURED EXPLICITLY

    create_engine's defaults assume a database on the other side of a stable
    local network. This one runs on Railway, behind a managed proxy that
    closes idle connections, and the application itself is idle most of the
    time. That combination produces the classic managed-Postgres symptom: the
    first request after a quiet period fails on a connection the pool still
    believes is open, and the second succeeds.

    pool_pre_ping is the fix. It costs one trivial round trip per checkout and
    turns a user-visible 500 into a transparent reconnect. pool_recycle caps
    how long a connection may live at all, so connections are retired on our
    schedule rather than discovered dead on the proxy's.

    The sizes are stated rather than inherited because FastAPI runs these sync
    endpoints in a threadpool, so concurrency here is real. The defaults
    (pool_size 5, max_overflow 10) are close to right for this workload, but a
    reader should not have to know SQLAlchemy's defaults to know what this
    service will do under load.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import settings

DATABASE_URL = settings.db_url

engine = create_engine(
    DATABASE_URL,
    # Verify a pooled connection is alive before handing it out. Without this,
    # an idle-timed-out connection surfaces as a 500 on the next request.
    pool_pre_ping=True,
    # Retire connections after 30 minutes, comfortably inside the idle
    # timeouts managed providers typically impose.
    pool_recycle=1800,
    pool_size=5,
    max_overflow=10,
    # Wait rather than fail instantly when every connection is checked out,
    # but do not wait so long that a request hangs past any sensible timeout.
    pool_timeout=30,
)

SessionLocal = sessionmaker(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
