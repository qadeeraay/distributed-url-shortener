import pytest
from app.core.bloom import RedisBloomFilter


class MockRedis:
    """In-memory mock for Redis bitset operations."""
    def __init__(self):
        self.bitsets = {}

    def pipeline(self):
        return self

    def setbit(self, name, offset, value):
        if name not in self.bitsets:
            self.bitsets[name] = {}
        self.bitsets[name][offset] = value
        return self

    def getbit(self, name, offset):
        val = self.bitsets.get(name, {}).get(offset, 0)
        if not hasattr(self, "_ops"):
            self._ops = []
        self._ops.append(val)
        return self

    async def execute(self):
        res = getattr(self, "_ops", [])
        self._ops = []
        return res


@pytest.mark.asyncio
async def test_bloom_filter_deterministic_hashing():
    mock_redis = MockRedis()
    bloom = RedisBloomFilter(mock_redis, filter_name="test_bloom", size_bits=100_000, hash_count=5)

    offsets1 = bloom._get_hash_offsets("code_123")
    offsets2 = bloom._get_hash_offsets("code_123")
    offsets3 = bloom._get_hash_offsets("code_456")

    # Hash must be deterministic for identical keys
    assert offsets1 == offsets2
    assert len(offsets1) == 5
    # Different keys must produce different bit vectors
    assert offsets1 != offsets3
    # Offsets must be within range [0, size_bits)
    for offset in offsets1:
        assert 0 <= offset < 100_000


@pytest.mark.asyncio
async def test_bloom_filter_membership():
    mock_redis = MockRedis()
    bloom = RedisBloomFilter(mock_redis, filter_name="test_bloom", size_bits=100_000, hash_count=5)

    key = "short123"
    await bloom.add(key)

    # Key that was added should report present
    is_present = await bloom.contains(key)
    assert is_present is True
