# InvoiceLine AI — REST API v1 Reference

Base URL:
- Local development: `http://localhost:8000`
- Production: `https://api.invoiceline.ai`

All responses include `X-Request-Id`. Errors follow a consistent envelope:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Human-readable error description",
    "request_id": "req_01abc...",
    "details": null
  }
}
```

## Authentication

Pass an API key (`il_live_...`) or session token via `Authorization: Bearer <token>`.
Browser sessions also use an `HttpOnly`, `SameSite=Lax` cookie (`il_session`).

## Endpoints

### Health
- `GET /v1/health`

### Authentication & Organisation
- `POST /v1/auth/register` — Create user, organisation, owner membership, and session
- `POST /v1/auth/login` — Authenticate and establish session
- `POST /v1/auth/logout` — Revoke current session
- `GET /v1/auth/me` — Active user, organisation, role, and permissions
- `POST /v1/auth/password-reset/request` — Request single-use reset token
- `POST /v1/auth/password-reset/confirm` — Apply new password using reset token
- `GET /v1/organisation` / `PATCH /v1/organisation` — View or update organisation settings & retention days
- `GET /v1/organisation/members` / `POST /v1/organisation/members` / `PATCH /v1/organisation/members/{id}` / `DELETE /v1/organisation/members/{id}`

### API Keys (Session owner/admin only)
- `GET /v1/api-keys`
- `POST /v1/api-keys` — Returns `secret_key` **once**
- `DELETE /v1/api-keys/{id}` — Revokes key immediately

### Invoices
- `POST /v1/invoices` — Multipart upload (`file` or `files`, PDF/JPEG/PNG up to 15 MB). Supports `Idempotency-Key` header for single-file calls.
- `GET /v1/invoices` — Query params: `q`, `status`, `validation_status`, `sort_by`, `sort_dir`, `limit`, `offset`.
- `GET /v1/invoices/{id}` — Full structured extraction, line items, validation results, audit history, and short-lived signed `document_url`.
- `GET /v1/invoices/{id}/status` — Processing status & extraction errors.
- `GET /v1/invoices/{id}/results` — Extracted fields, line items, and validation findings.
- `POST /v1/invoices/{id}/validate` — Re-run deterministic financial validation rules.
- `POST /v1/invoices/{id}/review` — Save corrected fields/line items and/or `approve` / `reject`. Approval is blocked if any `failed` validation rule exists.
- `GET /v1/invoices/{id}/export?format=csv|json` — Download structured CSV or JSON.
- `GET /v1/invoices/{id}/document?token=...` — Stream private invoice binary using signed token or Bearer auth.
- `DELETE /v1/invoices/{id}` — Soft-delete invoice and delete private R2 object.

### Usage & Webhooks
- `GET /v1/usage` — Invoice counts by status, total pages processed, 30-day API request count, recent activity, and recent API request logs.
- `POST /v1/webhooks` — Register webhook URL & subscribed events (`invoice.extracted`, `invoice.failed`, `invoice.approved`, `invoice.rejected`). Returns `signing_secret` **once**.
- `GET /v1/webhooks` — List webhooks.
- `DELETE /v1/webhooks/{id}` — Delete webhook.
- `GET /v1/webhooks/deliveries` — Recent webhook delivery attempts and HTTP responses.

## Webhook Signature Verification

Every delivery includes `X-InvoiceLine-Signature: t=<unix_timestamp>,v1=<hex_hmac_sha256>`.
Compute `HMAC_SHA256(signing_secret, f"{timestamp}.{raw_request_body}")` and compare in constant time.
