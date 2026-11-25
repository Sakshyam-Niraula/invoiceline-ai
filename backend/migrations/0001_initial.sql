-- InvoiceLine AI initial relational schema (Cloudflare D1 / SQLite)

CREATE TABLE IF NOT EXISTS organisations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    retention_days INTEGER NOT NULL DEFAULT 365,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'reviewer', 'viewer')),
    created_at TEXT NOT NULL,
    UNIQUE (organisation_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scopes TEXT NOT NULL DEFAULT 'invoices:read,invoices:write',
    last_used_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_keys_org ON api_keys(organisation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    uploaded_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    file_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_sha256 TEXT NOT NULL,
    r2_key TEXT NOT NULL,
    page_count INTEGER,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'extracted', 'needs_review', 'approved', 'rejected', 'failed')),
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (validation_status IN ('pending', 'passed', 'warning', 'failed', 'requires_review')),
    extraction_provider TEXT,
    extraction_error TEXT,
    processing_started_at TEXT,
    processing_completed_at TEXT,
    supplier_name TEXT,
    supplier_address TEXT,
    supplier_tax_id TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    due_date TEXT,
    currency TEXT,
    po_number TEXT,
    subtotal TEXT,
    tax_total TEXT,
    discount_total TEXT,
    total_amount TEXT,
    payment_terms TEXT,
    payment_details_json TEXT,
    field_metadata_json TEXT,
    raw_extraction_json TEXT,
    reviewed_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_org_idempotency
    ON invoices(organisation_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_invoices_org_status_created
    ON invoices(organisation_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_invoices_org_sha256
    ON invoices(organisation_id, file_sha256);

CREATE INDEX IF NOT EXISTS idx_invoices_org_inv_number
    ON invoices(organisation_id, invoice_number);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    line_index INTEGER NOT NULL,
    page_number INTEGER,
    description TEXT NOT NULL DEFAULT '',
    sku TEXT,
    quantity TEXT,
    unit TEXT,
    unit_price TEXT,
    discount TEXT,
    tax_rate TEXT,
    tax_amount TEXT,
    line_total TEXT,
    confidence REAL,
    source_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_line_items_invoice ON invoice_line_items(invoice_id, line_index);

CREATE TABLE IF NOT EXISTS validation_results (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('passed', 'warning', 'failed', 'requires_review')),
    field_path TEXT,
    line_index INTEGER,
    message TEXT NOT NULL,
    expected_value TEXT,
    actual_value TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_invoice ON validation_results(invoice_id, created_at);

CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    signing_secret_enc TEXT NOT NULL,
    secret_prefix TEXT NOT NULL,
    events_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhooks_org ON webhooks(organisation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    webhook_id TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    invoice_id TEXT REFERENCES invoices(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    response_status INTEGER,
    response_body TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed')),
    delivered_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_org ON webhook_deliveries(organisation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'api_key', 'system')),
    actor_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    metadata_json TEXT,
    ip_address TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_org_created ON audit_logs(organisation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(organisation_id, resource_type, resource_id, created_at ASC);

CREATE TABLE IF NOT EXISTS api_request_logs (
    id TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    api_key_id TEXT REFERENCES api_keys(id) ON DELETE SET NULL,
    request_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    pages_processed INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_req_org_created ON api_request_logs(organisation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS rate_limits (
    bucket_key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
);
