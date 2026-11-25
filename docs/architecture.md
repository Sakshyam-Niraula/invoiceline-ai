# InvoiceLine AI — Architecture & Technical Decisions

## 1. Environment Inspection & Compatibility Verification

Verified on the target development environment:
- **Node.js**: `v24.14.1`, **npm**: `11.11.0`
- **Python**: `3.11.9` (local `.venv` provisioned with FastAPI, Pydantic, httpx, python-multipart, pytest, ruff, uvicorn)
- **Cloudflare packages verified on npm**: `wrangler@4.168.0`, `vinext@1.4.6`, `@cloudflare/vite-plugin@1.70.2`, `next@16.4.6`

---

## 2. Final Stack & Cloudflare Runtime Evaluation

### 2.1 Frontend: Next.js + TypeScript on `vinext` (Cloudflare Workers)
- **Evaluated `vinext` first**: `vinext@1.4.6` is Cloudflare's recommended Vite-powered Next.js App Router runtime (`@cloudflare/vite-plugin` + Vite 8 + React 19).
- **Capabilities used**: App Router pages, client/server boundary, Tailwind CSS, shadcn/ui-style accessible Radix/HTML primitives, TanStack Table, TanStack Query, React Hook Form + Zod, strict TypeScript (`"strict": true`).
- **Fallback rule**: If any App Router or Windows build edge case proves unreliable under `vinext`, `@opennextjs/cloudflare` is the documented fallback. During Stage 1/4, `vinext check` and `vinext build` are executed directly to verify compatibility.

### 2.2 Backend: FastAPI on Cloudflare Python Workers
- **Evaluated Cloudflare Python Workers**: Python Workers support FastAPI natively via `workers.asgi.entrypoint(app)` and Queue consumption via `WorkerEntrypoint.queue(self, batch)`, with direct bindings to D1 (`env.DB`), R2 (`env.BUCKET`), Queues (`env.INVOICE_QUEUE`), and Secrets.
- **Why Python Workers is viable without Containers**:
  - Core backend dependencies (`fastapi`, `pydantic`, `httpx`, `python-multipart`) are pure-Python / Wasm-compatible.
  - Cryptography (`PBKDF2-HMAC-SHA256`, `HMAC-SHA256`, Encrypt-then-MAC `SHA256-CTR + HMAC-SHA256` for webhook secret encryption at rest, signed file URL tokens, session/API key hashing) uses Python 3.11 standard library (`hashlib`, `hmac`, `secrets`, `base64`) with zero C extensions.
  - Financial calculations use Python stdlib `decimal.Decimal` for exact arithmetic.
  - Document extraction delegates heavy vision/PDF OCR over HTTPS (`httpx`) to external Document AI providers rather than embedding Tesseract/Poppler/PyMuPDF in the Worker isolate.
  - **Container trigger**: If an organisation later requires self-hosted local OCR binaries (e.g., Tesseract, Docling, or PyMuPDF rendering), those specific workloads should move to Cloudflare Containers; the current architecture keeps the `ExtractionProvider` interface isolated so that swap requires no API or DB changes.

### 2.3 Database: Cloudflare D1 (SQLite)
- **Suitability**: D1 supports relational tables, foreign keys, unique partial indexes, parameterized prepared statements, and batch transactions (`db.batch([...])`).
- **ORM Decision**: SQLAlchemy is omitted. D1's Worker binding exposes an async prepared-statement API (`env.DB.prepare(...).bind(...).all()/first()/run()`). Using a thin, typed repository layer over an abstract `Database` protocol allows identical parameterized SQL queries to run against Cloudflare D1 in Workers and Python `sqlite3` in local uvicorn development and `pytest`.
- **PostgreSQL escalation criteria**: D1 would become unsuitable only if high concurrent write throughput exceeds single-primary SQLite write limits (>500 writes/sec sustained) or multi-region active-active writes are required; at that point, migrating the `Database` adapter to Managed PostgreSQL via Cloudflare Hyperdrive requires no service-layer changes.

### 2.4 Storage, Queues, Abuse Protection & Secrets
- **Cloudflare R2**: Private bucket (`invoiceline-documents`) storing uploaded PDFs/images under `orgs/{organisation_id}/invoices/{invoice_id}/{sha256_prefix}_{safe_filename}`. No public bucket access; document downloads use short-lived HMAC-SHA256 signed backend proxy URLs validated per organisation and expiry timestamp.
- **Cloudflare Queues**: Asynchronous invoice extraction & validation queue (`invoiceline-extraction`) decoupling upload latency from external Document AI calls, with `max_retries = 3` and dead-letter handling (`invoiceline-extraction-dlq`). In local uvicorn development without Wrangler queue runtime, an explicit async background task runner invokes the exact same `InvoiceProcessingService.process_invoice()` pipeline.
- **Cloudflare Workflows**: Not required. Invoice extraction + deterministic validation is a single bounded idempotent pipeline step per queued message; Queues retries handle transient provider errors cleanly without extra workflow state overhead.
- **Cloudflare Turnstile**: Integrated on registration, login, and password reset endpoints (verifying `cf-turnstile-response` against `https://challenges.cloudflare.com/turnstile/v0/siteverify` when `TURNSTILE_SECRET_KEY` is configured).
- **Cloudflare Secrets**: `APP_SECRET`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `TURNSTILE_SECRET_KEY` stored as Wrangler Secrets (never committed).

---

## 3. External Document AI Provider Evaluation

| Provider | Cost / 1,000 Pages | Multi-page PDF & Tables | Confidence & Source Refs | Worker Compatibility | Decision |
| --- | --- | --- | --- | --- | --- |
| **Google Gemini (`gemini-2.5-flash`)** | ~$1.50 – $3.50 | Native PDF + image multimodal input; handles cross-page tables | Schema-enforced field confidence & page/source snippet | Pure HTTPS REST (`httpx`) + API key | **Primary Default** |
| **Mistral OCR (`mistral-ocr-latest`)** | ~$5.00 | Strong layout/table markdown OCR | Page/block annotations; needs second structuring pass | Pure HTTPS REST (`httpx`) + API key | **Supported Secondary Adapter** |
| **Google Document AI (Invoice Parser)** | ~$20.00 – $30.00 | Strong standard invoice layout | Native field confidence + bounding polygons | Requires GCP OAuth2 JWT signing | Future Enterprise Adapter |
| **Amazon Textract (`AnalyzeExpense`)** | ~$8.00 – $10.00 | Strong English expense tables | Native field confidence | Requires AWS SigV4 + S3 staging | Not selected (AWS coupling) |

All providers implement the abstract `ExtractionProvider` protocol (`app/services/extraction/base.py`), returning a normalized `ExtractedInvoicePayload` with header fields, payment details, multi-page `ExtractedLineItem[]`, per-field confidence indicators, and source page/snippet references.

---

## 4. Database Schema Rationale (`backend/migrations/0001_initial.sql`)

Minimal relational schema avoiding redundant tables:
- `organisations`, `users`, `memberships`: multi-tenant organisation isolation with RBAC (`owner`, `admin`, `reviewer`, `viewer`).
- `sessions`, `password_reset_tokens`: hashed opaque bearer/cookie sessions and single-use password reset tokens.
- `api_keys`: stores `key_prefix` + SHA-256 `key_hash` + scopes; raw key shown once at creation.
- `invoices`: document metadata, `idempotency_key` (unique partial index per org), `file_sha256` (duplicate file detection), `r2_key`, processing timestamps/errors, extracted header fields, JSON metadata (`payment_details_json`, `field_metadata_json`, `raw_extraction_json`), review decision, and `deleted_at` soft delete.
- `invoice_line_items`: multi-page line items (`page_number`, `line_index`, `description`, `sku`, `quantity`, `unit`, `unit_price`, `discount`, `tax_rate`, `tax_amount`, `line_total`, `confidence`, `source_reference`).
- `validation_results`: deterministic rule outcomes (`passed`, `warning`, `failed`, `requires_review`) with expected vs actual values and field/line pointers.
- `webhooks`, `webhook_deliveries`: encrypted signing secrets (`signing_secret_enc`), subscribed events, and delivery attempt history.
- `audit_logs`: processing history, uploads, corrections, approvals, API key/webhook changes.
- `api_request_logs`, `rate_limits`: real usage/latency/pages processed metrics and sliding/fixed-window rate limiting.

---

## 5. Repository Structure

```text
invoiceline-ai/
├── docs/
│   ├── architecture.md
│   └── api.md
├── backend/
│   ├── migrations/
│   │   └── 0001_initial.sql
│   ├── app/
│   │   ├── main.py              # FastAPI app + CORS + middleware + routers
│   │   ├── worker.py            # Cloudflare Python Worker ASGI + Queue entrypoint
│   │   ├── config.py            # Environment / Cloudflare binding settings
│   │   ├── db/                  # Database protocol, D1 adapter, SQLite local adapter
│   │   ├── storage/             # Private storage protocol, R2 adapter, local disk adapter
│   │   ├── queue/               # Queue publisher protocol, CF Queue & local async runner
│   │   ├── core/                # Crypto, password hashing, signed URLs, rate limiting, errors
│   │   ├── schemas/             # Pydantic request/response & extraction schemas
│   │   ├── services/            # Auth, orgs, invoices, extraction, validation, webhooks, usage
│   │   └── api/                 # Versioned /v1/* + auth + workspace routes
│   ├── tests/
│   ├── pyproject.toml
│   └── wrangler.toml
└── frontend/
    ├── src/
    │   ├── app/                 # Next.js App Router pages (vinext)
    │   ├── components/          # UI primitives & feature components
    │   ├── lib/                 # Typed API client, auth context, formatters, Zod schemas
    │   └── types/               # Strict TypeScript API contracts
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    └── wrangler.jsonc
```
