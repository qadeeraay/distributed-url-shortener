import unittest

try:
    import httpx
    import aiosqlite
    from httpx import AsyncClient, ASGITransport
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.main import app
    from app.db.models import Base
    from app.db.session import get_db
    HAS_DEPS = bool(httpx and aiosqlite)
except ImportError:
    HAS_DEPS = False

if HAS_DEPS:
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


@unittest.skipUnless(HAS_DEPS, "Integration test dependencies (httpx/aiosqlite) not installed in runtime")
class TestUrlShortenerAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def test_healthcheck(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")
            self.assertIn(response.status_code, (200, 503))
            data = response.json()
            self.assertIn("status", data)
            self.assertIn("services", data)

    async def test_metrics_endpoint(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/metrics")
            self.assertEqual(response.status_code, 200)
            self.assertIn("http_requests_total", response.text)

    async def test_create_and_redirect_url(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Create short URL
            payload = {"url": "https://deepmind.google/technologies/gemini/"}
            create_resp = await client.post("/api/v1/urls", json=payload)
            self.assertEqual(create_resp.status_code, 201)
            data = create_resp.json()
            self.assertIn("short_code", data)
            short_code = data["short_code"]
            self.assertGreaterEqual(len(short_code), 4)
            self.assertEqual(data["original_url"], "https://deepmind.google/technologies/gemini/")
            self.assertFalse(data["is_custom"])

            # 2. Redirect
            redirect_resp = await client.get(f"/r/{short_code}", follow_redirects=False)
            self.assertEqual(redirect_resp.status_code, 302)
            self.assertEqual(redirect_resp.headers["location"], "https://deepmind.google/technologies/gemini/")

            # 3. Retrieve metadata
            meta_resp = await client.get(f"/api/v1/urls/{short_code}")
            self.assertEqual(meta_resp.status_code, 200)
            self.assertEqual(meta_resp.json()["short_code"], short_code)

            # 4. Retrieve analytics
            analytics_resp = await client.get(f"/api/v1/urls/{short_code}/analytics")
            self.assertEqual(analytics_resp.status_code, 200)
            adata = analytics_resp.json()
            self.assertEqual(adata["short_code"], short_code)
            self.assertIn("total_clicks", adata)

    async def test_custom_alias_and_collision(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "url": "https://github.com",
                "custom_alias": "my-github-link"
            }
            res = await client.post("/api/v1/urls", json=payload)
            self.assertEqual(res.status_code, 201)
            self.assertEqual(res.json()["short_code"], "my-github-link")
            self.assertTrue(res.json()["is_custom"])

            # Attempt duplicate creation -> 409 Conflict
            res_dup = await client.post("/api/v1/urls", json=payload)
            self.assertEqual(res_dup.status_code, 409)
            self.assertIn("already in use", res_dup.json()["detail"])

    async def test_reserved_alias_rejection(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "url": "https://github.com",
                "custom_alias": "metrics"
            }
            res = await client.post("/api/v1/urls", json=payload)
            self.assertIn(res.status_code, (400, 422))

    async def test_nonexistent_url_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/r/nonexistent999", follow_redirects=False)
            self.assertEqual(res.status_code, 404)

    async def test_soft_delete(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/v1/urls", json={"url": "https://wikipedia.org", "custom_alias": "wiki-temp"})
            self.assertEqual(res.status_code, 201)

            del_res = await client.delete("/api/v1/urls/wiki-temp")
            self.assertEqual(del_res.status_code, 204)

            redir_res = await client.get("/r/wiki-temp", follow_redirects=False)
            self.assertEqual(redir_res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
