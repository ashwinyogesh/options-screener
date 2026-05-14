# ADR-0018: Classifier — Embedding Soft-Fail, Backfill Loop, and Model Switch

- **Status**: Accepted
- **Date**: 2026-05-14
- **Supersedes (in part)**: [ADR-0017](0017-narrative-phase5-detector.md) — `embedding_model` default and "Embedding generation" error-handling subsection.

## Context

[ADR-0017](0017-narrative-phase5-detector.md) decided to co-generate embeddings inside `job-classifier`
(Option A) and to co-locate them on the `signals` Cosmos document (Option C). That ADR was
written before the worker shipped. Standing up the job revealed four operational decisions that
the original ADR either left under-specified or specified incorrectly:

1. **Embedding-failure policy** — ADR-0017 noted only that "a failure in the embedding call must
   not block conviction-state storage — error handling must be written carefully". The exact
   contract was not pinned, and the first implementation could in principle have aborted the
   whole batch on a 4xx from the embedding endpoint.
2. **Stale-document backfill** — Signals that were classified during the brief window when the
   embedding endpoint was misconfigured (a malformed `openai-endpoint` Key Vault secret pointing
   to the full chat-completions URL rather than the bare host) ended up with `conviction_state`
   set but no `embedding`. ADR-0017 had no mechanism to recover these.
3. **Embedding model** — ADR-0017 names `text-embedding-3-small`. That deployment was never
   stood up in the project's Azure OpenAI resource; only `gpt-4o-mini`, `text-embedding-ada-002`,
   and `text-embedding-3-large` exist. The methodology doc and ADR-0017 fixed `3-small` as the
   model name; the implementation must use what is actually deployed.
4. **HTTP client choice** — A brief detour using a raw `httpx.Client` to call the embeddings
   REST endpoint, motivated by what looked like an `openai` SDK routing bug, was reverted once
   the real root cause (the malformed KV secret above) was found. Worth recording so future
   maintainers do not re-introduce the bypass.

The 72-hour window query in `job-narrative-detector` already filters
`WHERE IS_DEFINED(c.embedding)`, so documents without an embedding are simply skipped — they
are not silently mis-clustered. This makes soft-failure operationally safe.

---

## Decisions

### 1. Embedding failure is soft; conviction-state writes always proceed

The classifier processes signals in chunks of `BATCH_SIZE` (default 50). Each chunk does:

```
rationales = [doc.rationale for doc in chunk]
try:
    vecs = embedder.embed_batch(rationales)
except Exception:
    log + continue with embeddings=[None] * len(chunk)
for each doc in chunk:
    state, conf = classifier.classify(...)
    cosmos.write_conviction(doc, state, conf, embedding=embeddings[idx], ...)
```

The chat (classification) call and the embedding call are independent. An embedding failure
logs `logger.exception(...)` and falls through with `embedding=None`; `write_conviction`
omits the `embedding` / `embedding_model` fields entirely when `embedding is None` rather
than writing `null`. This keeps the schema discriminator clean for the backfill query
(`NOT IS_DEFINED(c.embedding)`).

**Why not retry inside the chunk?** Tenacity-style retry is already applied to the Cosmos
writes ([cosmos_client.py](../../workers/classifier/cosmos_client.py)). Adding a second
retry layer around the OpenAI calls would compound latency inside a 25-minute replica
timeout and rarely change the outcome (auth errors and quota errors are not transient).
The 30-minute cron is itself the retry.

**Conviction-only failure (chat raised)** is *not* soft. The signal id is added to a
`_skipped_ids` set so the next inner-loop fetch excludes it, and the job exits with code 1
if every attempted signal failed (`classified == 0 and skipped > 0`). This surfaces as a
Container Apps Job failure and triggers the on-call alert.

### 2. Backfill loop with progress guard

After the main classify loop, the worker runs a second pass:

```
SELECT * FROM c
WHERE IS_DEFINED(c.conviction_state)
  AND NOT IS_DEFINED(c.embedding)
ORDER BY c._ts ASC OFFSET 0 LIMIT @batch_size
```

For each batch returned, embeddings are computed and written back via
`write_embedding(doc, vec, embedding_model)`. The loop terminates on any of:

- Empty fetch result.
- Fetch returns fewer than `batch_size` items (drained).
- `embed_batch` raises — abort and try again on the next cron.
- **Progress guard**: if the next fetch returns only ids already processed this run
  (`seen_backfill_ids` set), `logger.warning(...)` and break. This protects against a
  silent-write no-op (e.g. a future Cosmos indexing change that strips `/embedding`, or a
  code bug in `write_embedding`) which would otherwise spin until `replicaTimeout: 1500`.

Backfill does **not** re-run classification. `conviction_state` is preserved as-is.

### 3. Default embedding deployment is `text-embedding-ada-002`

[classifier.py](../../workers/classifier/classifier.py) hard-codes the default as
`text-embedding-ada-002` (1 536-dim, matches the dimensionality `job-narrative-detector`
expects for HDBSCAN; matches the Phase 2 Bicep `excludedPaths` declaration on `/embedding/?`).
The default is overridable via the Key Vault secret `embed-deployment` to support future
model migration without a code change. The `embedding_model` field is written on every signal
document so a future migration can identify which vectors are produced by which model and
re-embed accordingly.

The methodology doc and ADR-0017 references to `text-embedding-3-small` are superseded.
ADR-0017's Pros/Cons sections still apply (Option A and Option C are unchanged); only the
specific model name and the "100K TPM" capacity number need updating.

### 4. Both chat and embeddings use the `openai` Python SDK

`ConvictionClassifier` uses `AzureOpenAI.chat.completions.create(...)` (api version
`2024-08-01-preview` for structured-output `response_format` support).

`EmbeddingGenerator` uses `AzureOpenAI.embeddings.create(...)` on a separate client instance
(api version `2024-02-01`, the embeddings GA). Two clients in one process is fine; the brief
detour to raw `httpx` was unnecessary.

The api-version split is intentional: chat needs the preview for structured outputs;
embeddings is happier on the stable GA. The two clients share the same `api_key` and
`endpoint` from Key Vault.

---

## Consequences

- **Methodology doc must be updated**: [NARRATIVE_METHODOLOGY.md §Phase 5](../NARRATIVE_METHODOLOGY.md)
  currently says `text-embedding-3-small (100K TPM)` and `pgvector index on post_embeddings`.
  Replace with `text-embedding-ada-002` (overridable via `embed-deployment` KV secret) and
  remove the pgvector reference (decided in ADR-0017 — embeddings live on `signals`).
- **Operational alert** stays simple: a job-classifier non-zero exit means *classification*
  failed for every attempted signal. Embedding-only failures are visible in logs as
  `Embedding batch failed for N signals — conviction writes proceed without embedding`, and
  recovered on the next cron via the backfill loop.
- **Schema discriminator stays binary**: presence vs absence of the `embedding` field is the
  signal. We never write `embedding=null` or `embedding=[]`.
- **Cost**: backfill loop runs every 30 minutes whether needed or not (a `SELECT TOP 50` on
  `signals` filtered by two `IS_DEFINED` predicates). Cosmos serverless cost is negligible
  (~1 RU per query); the OpenAI cost is zero when nothing matches.
- **No new infra**: this ADR is implementation-only. Cosmos schema, KV secret names, and the
  Container Apps Job definition are unchanged from ADR-0013 / ADR-0017.

## Validation

Unit tests under [workers/classifier/tests/](../../workers/classifier/tests/) cover, with all
external services mocked:

- Embedding API failure does not block `write_conviction`.
- `write_conviction(..., embedding=None)` omits the `embedding` and `embedding_model`
  fields entirely.
- `write_conviction(..., embedding=[...])` requires `embedding_model` (no silent default).
- Backfill terminates on empty fetch, short batch, exception, and the progress-guard stall.
- `sys.exit(1)` only when `classified == 0 and skipped > 0`.
- 10 conviction states locked in (regression guard against drift from methodology doc §3).
