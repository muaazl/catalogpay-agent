import os

# Must run before any app module is imported so module-level settings/engine use test values.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["ENVIRONMENT"] = "test"
# Tests never auto-serve a locally built frontend/dist; Feature 5 tests opt in explicitly.
os.environ["STATIC_DIST_DIR"] = os.path.join(os.path.dirname(__file__), "__no_dist__")
os.environ["SEED_DEMO_ORDERS"] = "false"
# Never let a developer's real key make tests hit the network; the opt-in live test reads it here.
os.environ["CATALOGPAY_LIVE_GEMINI_KEY"] = os.environ.get("GEMINI_API_KEY", "")
os.environ["GEMINI_API_KEY"] = ""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, joinedload

import app.models  # noqa: F401  (register models on metadata)
from app.db.base import Base
from app.db.session import build_engine, build_session_factory, get_db
from app.main import create_app
from app.models.catalog import ProductSKU
from app.services.seed import seed_catalog
from app.services.sku_matcher import HybridSKUMatcher


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = build_session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture
def seeded_session(session: Session) -> Session:
    seed_catalog(session)
    return session


@pytest.fixture
def matcher(seeded_session: Session) -> HybridSKUMatcher:
    skus = seeded_session.scalars(
        select(ProductSKU).options(joinedload(ProductSKU.vendor)).order_by(ProductSKU.sku_code)
    ).all()
    return HybridSKUMatcher.from_skus(skus)


@pytest.fixture
def client(engine: Engine, seeded_session: Session) -> Iterator[TestClient]:
    factory = build_session_factory(engine)

    def _override_get_db() -> Iterator[Session]:
        with factory() as s:
            yield s

    application = create_app()
    application.dependency_overrides[get_db] = _override_get_db
    with TestClient(application) as tc:
        yield tc
