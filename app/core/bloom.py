import hashlib
import redis.asyncio as redis
from typing import List
from app.config import settings


class RedisBloomFilter:
    """
    Algorithmic Bitset Bloom Filter directly implemented on top of standard Redis bit operations (SETBIT/GETBIT).
    Zero external C-extension or RedisBloom module required.
    
    Protects downstream PostgreSQL database from 'Cache Penetration' attacks where malicious
    actors flood requests for non-existent short codes to induce DB query exhaustion.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        filter_name: str = settings.BLOOM_FILTER_NAME,
        size_bits: int = settings.BLOOM_FILTER_SIZE_BITS,
        hash_count: int = settings.BLOOM_HASH_COUNT,
    ):
        self.redis = redis_client
        self.filter_name = filter_name
        self.size_bits = size_bits
        self.hash_count = hash_count

    def _get_hash_offsets(self, key: str) -> List[int]:
        """
        Generates `hash_count` independent bit offsets for a given string key
        using double-hashing (Kirsch-Mitzenmacher optimization over SHA-256 & MD5).
        hash_i(x) = (hash1(x) + i * hash2(x)) mod m
        """
        hash1 = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
        hash2 = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)

        offsets = []
        for i in range(self.hash_count):
            combined_hash = (hash1 + i * hash2) % self.size_bits
            offsets.append(combined_hash)
        return offsets

    async def add(self, key: str) -> None:
        """
        Adds a key to the Bloom filter by setting the corresponding bits in Redis.
        Uses pipelining for minimal round-trip network latency.
        """
        offsets = self._get_hash_offsets(key)
        pipe = self.redis.pipeline()
        for offset in offsets:
            pipe.setbit(self.filter_name, offset, 1)
        await pipe.execute()

    async def contains(self, key: str) -> bool:
        """
        Checks whether a key might exist in the Bloom filter.
        Returns:
            False -> Key DEFINITELY does NOT exist (100% mathematical certainty).
            True  -> Key PROBABLY exists (subject to false positive rate < 1%).
        """
        offsets = self._get_hash_offsets(key)
        pipe = self.redis.pipeline()
        for offset in offsets:
            pipe.getbit(self.filter_name, offset)
        results = await pipe.execute()

        # If any bit is 0, the element is definitely not in the set
        return all(bit == 1 for bit in results)
