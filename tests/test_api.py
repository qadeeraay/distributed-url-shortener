import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.main import app
from app.db.models import Base
from app.db.session import get_db

# Use in-memory SQLite for high-speed, self-contained integration tests
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
async def prepare_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_healthcheck():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
        assert response.status_code in (200, 503)
        data = response.json()
        assert "status" in data
        assert "services" in data


@pytest.mark.asyncio
async def test_metrics_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "http_requests_total" in response.text


@pytest.mark.asyncio
async def test_create_and_redirect_url():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create short URL with auto-generated Base62 code
        payload = {"url": "https://deepmind.google/technologies/gemini/"}
        create_resp = await client.post("/api/v1/urls", json=payload)
        assert create_resp.status_code == 201
        data = create_resp.json()
        assert "short_code" in data
        short_code = data["short_code"]
        assert len(short_code) >= 4
        assert data["original_url"] == "https://deepmind.google/technologies/gemini/"
        assert data["is_custom"] is False

        # 2. Redirect
        redirect_resp = await client.get(f"/r/{short_code}", follow_redirects=False)
        assert redirect_resp.status_code == 302
        assert redirect_resp.headers["location"] == "https://deepmind.google/technologies/gemini/"

        # 3. Retrieve metadata
        meta_resp = await client.get(f"/api/v1/urls/{short_code}")
        assert meta_resp.status_code == 200
        assert meta_resp.json()["short_code"] == short_code

        # 4. Retrieve analytics
        analytics_resp = await client.get(f"/api/v1/urls/{short_code}/analytics")
        assert analytics_resp.status_code == 200
        adata = analytics_resp.json()
        assert adata["short_code"] == short_code
        assert "total_clicks" in adata


@pytest.mark.asyncio
async def test_custom_alias_and_collision():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create URL with custom alias
        payload = {
            "url": "https://github.com",
            "custom_alias": "my-github-link"
        }
        res = await client.post("/api/v1/urls", json=payload)
        assert res.status_code == 201
        assert res.json()["short_code"] == "my-github-link"
        assert res.json()["is_custom"] is True

        # 2. Attempt duplicate creation -> 409 Conflict
        res_dup = await client.post("/api/v1/urls", json=payload)
        assert res_dup.status_code == 409
        assert "already in use" in res_dup.json()["detail"]


@pytest.mark.asyncio
async def test_reserved_alias_rejection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "url": "https://github.com",
            "custom_alias": "metrics"  # Reserved route keyword
        }
        res = await client.post("/api/v1/urls", json=payload)
        assert res.status_code == 422 or res.status_code == 400


@pytest.mark.asyncio
async def test_nonexistent_url_returns_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/r/nonexistent999", follow_redirects=False)
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_soft_delete():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create
        res = await client.post("/api/v1/urls", json={"url": "https://wikipedia.org", "custom_alias": "wiki-temp"})
        assert res.status_code == 201

        # Delete
        del_res = await client.delete("/api/v1/urls/wiki-temp")
        assert del_res.status_code == 204

        # Redirect should now fail with 404
        redir_res = await client.get("/r/wiki-temp", follow_redirects=False)
        assert redir_res.status_code == 404
