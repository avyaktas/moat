import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import get_db
from main import app
from models import Base, Company

TEST_DATABASE_URL = "postgresql+psycopg://avyaktasharma@localhost:5432/moat_test"

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=engine)


@pytest.fixture
def client():
    # Build a pristine schema, seed known data, hand out a client, then wipe.
    Base.metadata.create_all(engine)

    db = TestingSessionLocal()
    db.add(Company(ticker="MSFT", name="Microsoft", sector="Technology"))
    db.commit()
    db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)