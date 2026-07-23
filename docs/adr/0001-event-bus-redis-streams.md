# ADR-0001 — Event bus: Redis Streams (with abstraction for Kafka later)

**Status:** Accepted (M1)
**Date:** 2026-05-25

## Context

Components must communicate without direct coupling. We need at-least-once delivery,
consumer groups so we can scale a strategy worker horizontally, and a debuggable
substrate (CLI-inspectable streams).

Options considered:

1. **Redis Streams** — simple, fast, already required for caching; `XREADGROUP`
   gives us consumer groups out of the box; one binary in docker-compose.
2. **Kafka / Redpanda** — better durability and horizontal scale, but heavier to
   run and overkill for the foreseeable scale (≤10 strategies × ≤100 instruments).
3. **NATS JetStream** — middle ground, but less common; another binary to learn.
4. **Postgres LISTEN/NOTIFY** — would conflate transport and storage; no consumer
   groups, no ack semantics.

## Decision

Use **Redis Streams** for M1 → M9. Hide it behind an `EventBus` Protocol so a
`KafkaBus` can replace it in M10 if production scale requires it without touching
the consumers.

## Consequences

- Consumers must be idempotent (at-least-once delivery).
- Stream length is bounded by `stream_max_len` (default 1M entries, approximate
  trimming).
- For multi-DC or strict ordering across partitions, Kafka would be better — that's
  why the interface keeps `stream`-level granularity rather than baking in Redis-only
  concepts.

## Migration path

When we move to Kafka, `core/bus/kafka_bus.py` implements `EventBus`. Streams become
topics; consumer groups map 1:1. The only consumer-facing change is the connection
string in config.
