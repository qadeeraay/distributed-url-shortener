# System Architecture & Technical Design

## 1. High-Level Architecture Overview

The **Distributed URL Shortener & Analytics Platform** is engineered to resolve three classic challenges in distributed web infrastructure:
1. **Zero-Collision Key Generation:** Eliminating random collision-checking database loops ($O(1)$ bijective encoding).
2. **Sub-5ms Hot Path:** Offloading reads to Redis Cache-Aside combined with an algorithmic Bitset Bloom Filter to eliminate database cache penetration.
3. **Decoupled Asynchronous Analytics:** Separating the latency-critical redirect hot path from analytical writes using Redis Streams and consumer worker groups.

```mermaid
flowchart TD
    Client([HTTP Client / Browser])

    subgraph API_Gateway ["FastAPI Hot Path Gateway"]
        Router["/r/{short_code}"]
        CacheCheck{"1. Redis Cache Hit?"}
        BloomCheck{"2. Bloom Filter Exists?"}
        DBQuery[("3. PostgreSQL Lookup")]
        StreamPush["4. Emit to Redis Stream"]
    end

    subgraph Caching_Layer ["Distributed In-Memory Tier"]
        RedisCache[("Redis 7 Cache-Aside")]
        RedisBloom[("Redis Bitset Bloom Filter")]
        NegativeCache[("Negative Cache (60s TTL)")]
        RedisStream[("Redis Stream: stream:url_clicks")]
    end

    subgraph Analytics_Engine ["Async Analytics Tier"]
        Worker["Analytics Consumer Worker"]
        UAParser["User-Agent & Geo Parser"]
        HourlyRollup[("Hourly Rollup Aggregator")]
    end

    subgraph Storage_Tier ["Persistent Storage"]
        PostgresURL[("PostgreSQL: urls table")]
        PostgresEvents[("PostgreSQL: click_events")]
        PostgresRollup[("PostgreSQL: hourly_analytics")]
    end

    Client -->|GET /r/{short_code}| Router
    Router --> CacheCheck
    CacheCheck -->|YES: <1ms| StreamPush
    CacheCheck -->|NO| BloomCheck
    BloomCheck -->|NO: 100% Not Found| NegativeCache
    BloomCheck -->|YES: Candidate| DBQuery
    DBQuery --> PostgresURL
    DBQuery -->|Populate Cache & Bloom| RedisCache
    DBQuery --> StreamPush
    StreamPush -->|XADD non-blocking| RedisStream
    StreamPush -->|HTTP 302 Found| Client

    RedisStream -->|XREADGROUP| Worker
    Worker --> UAParser
    Worker --> HourlyRollup
    Worker --> PostgresEvents
    HourlyRollup --> PostgresRollup
```

---

## 2. Deep Dive: Bijective Base62 Key Generation

### The Problem with Naive Random Generation & MD5 Truncation
Naive architectures generate random 6-character strings (e.g., `crypto.randomBytes(6)` or `Math.random()`) or truncated cryptographic hashes (e.g., MD5/SHA256). As the dataset reaches millions of entries, birthday paradox collisions increase exponentially, forcing the application into database re-query retry loops ($O(N)$ worst-case).

### The Production Solution
We map a monotonic 64-bit integer sequence from PostgreSQL directly to Base62 (`[0-9a-zA-Z]`):

$$\text{Base62 Characters} = \{0\dots9, a\dots z, A\dots Z\} \quad (|S| = 62)$$

$$\text{Code} = \sum_{i=0}^{k-1} d_i \times 62^i$$

- **Zero Collisions:** Every unique integer $N$ maps to exactly one Base62 string $S$, and vice-versa.
- **Pre-Offsetting:** By offsetting IDs by $62^4 \approx 14,776,336$, generated short codes are guaranteed to have a minimum uniform length of 6 characters from day one.
- **Time Complexity:** $O(\log_{62} N) \approx O(1)$ with zero network overhead.

---

## 3. Hot Path Optimization & Cache Penetration Defense

### Cache Penetration Attack Vector
Malicious actors or bots frequently flood URL shorteners with millions of non-existent random keys (`/r/nonExistent123`). If unchecked, every cache miss bypasses Redis and queries PostgreSQL directly, causing connection pool starvation and database CPU spikes.

### The Two-Tier Defense
1. **Bitset Bloom Filter (Redis-backed):**
   - Configured with $m = 10,000,000$ bits and $k = 5$ independent hash functions (Kirsch-Mitzenmacher optimization using SHA-256 and MD5).
   - If `contains(code)` returns `False`, the key is **100% guaranteed not to exist**. The request is rejected immediately with HTTP 404 without querying PostgreSQL.
2. **Negative Caching:**
   - If a key passes the Bloom filter (due to false positive probability $p < 0.01$) but is missing from PostgreSQL, it is stored in Redis under `url:not_found:{code}` with a 60-second TTL. Subsequent queries within that window are served directly from memory.

---

## 4. Asynchronous Clickstream Ingestion via Redis Streams

Redirect latency directly impacts user perception and conversion. Writing raw click metadata, parsing user-agent strings, and calculating rollups inside the redirect request path degrades latency from 2ms to 150ms+.

### Decoupled Processing Pipeline
1. The `/r/{short_code}` endpoint non-blockingly fires an `XADD` command to Redis Stream `stream:url_clicks`:
   ```json
   {
     "short_code": "k8sA1z",
     "timestamp": "2026-09-11T00:45:00Z",
     "ip": "198.51.100.42",
     "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...",
     "referrer": "https://linkedin.com"
   }
   ```
2. The HTTP response immediately returns `302 Found` with the `Location` header.
3. The `AnalyticsStreamConsumer` worker operates within a persistent Redis Consumer Group (`cg:analytics_workers`):
   - Reads batches of events using `XREADGROUP`
   - Parses browser, platform, and device classification asynchronously
   - Generates a salted SHA-256 hash of the client IP for GDPR privacy compliance
   - Upserts hourly time-series buckets into PostgreSQL table `hourly_analytics`
   - Explicitly acknowledges messages with `XACK` to guarantee at-least-once delivery

---

## 5. Resilience & Failure Mode Matrix

| Component Failure | Blast Radius | Mitigation Mechanism |
| :--- | :--- | :--- |
| **Redis Cache Down** | Redirect latency increases from 2ms to 20ms | System gracefully falls back to direct PostgreSQL query; does not fail customer redirects. |
| **PostgreSQL Read Spike** | Potential connection exhaustion | Redis Cache-Aside absorbs >98% of redirect volume; pool pre-ping handles stale connections. |
| **Analytics Worker Down** | Click rollups temporarily delayed | Redis Stream buffers up to 100,000 click events in memory until worker restarts; zero event loss. |
| **Cache Stampede / Thundering Herd** | High concurrency miss on newly published URL | Redis setex with pre-computed payload during creation eliminates initial cold-start miss. |
