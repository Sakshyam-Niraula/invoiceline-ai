export type RoleType = "owner" | "admin" | "reviewer" | "viewer";

export type InvoiceStatus =
  | "queued"
  | "processing"
  | "extracted"
  | "needs_review"
  | "approved"
  | "rejected"
  | "failed";

export type ValidationStatus =
  | "pending"
  | "passed"
  | "warning"
  | "failed"
  | "requires_review";

export type ValidationSeverity = "passed" | "warning" | "failed" | "requires_review";

export interface UserSummary {
  id: string;
  email: string;
  full_name: string;
}

export interface OrganisationSummary {
  id: string;
  name: string;
  slug: string;
  retention_days: number;
}

export interface AuthSession {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: UserSummary;
  organisation: OrganisationSummary;
  role: RoleType;
}

export interface MeResponse {
  user: UserSummary;
  organisation: OrganisationSummary;
  role: RoleType;
  permissions: string[];
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: RoleType;
  created_at: string;
}

export interface ApiKeyItem {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface CreateApiKeyResponse extends ApiKeyItem {
  secret_key: string;
}

export interface FieldConfidence {
  confidence: number | null;
  source_reference: string | null;
}

export interface LineItem {
  id?: string | null;
  line_index: number;
  page_number: number | null;
  description: string;
  sku: string | null;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  discount: string | null;
  tax_rate: string | null;
  tax_amount: string | null;
  line_total: string | null;
  confidence?: number | null;
  source_reference: string | null;
}

export interface ValidationResult {
  id: string;
  rule_code: string;
  severity: ValidationSeverity;
  field_path: string | null;
  line_index: number | null;
  message: string;
  expected_value: string | null;
  actual_value: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface InvoiceSummary {
  id: string;
  file_name: string;
  content_type: string;
  file_size_bytes: number;
  file_sha256: string;
  page_count: number | null;
  status: InvoiceStatus;
  validation_status: ValidationStatus;
  extraction_provider: string | null;
  extraction_error: string | null;
  supplier_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  currency: string | null;
  total_amount: string | null;
  duplicate_file_detected: boolean;
  created_at: string;
  updated_at: string;
}

export interface InvoiceDetail extends InvoiceSummary {
  supplier_address: string | null;
  supplier_tax_id: string | null;
  po_number: string | null;
  subtotal: string | null;
  tax_total: string | null;
  discount_total: string | null;
  payment_terms: string | null;
  payment_details: Record<string, unknown> | null;
  field_metadata: Record<string, FieldConfidence>;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  document_url: string | null;
  line_items: LineItem[];
  validation_results: ValidationResult[];
  history: AuditEvent[];
}

export interface InvoiceListResponse {
  items: InvoiceSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface InvoiceUploadResponse {
  invoices: InvoiceSummary[];
}

export interface WebhookItem {
  id: string;
  url: string;
  secret_prefix: string;
  events: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateWebhookResponse extends WebhookItem {
  signing_secret: string;
}

export interface WebhookDelivery {
  id: string;
  webhook_id: string;
  invoice_id: string | null;
  event_type: string;
  request_id: string;
  response_status: number | null;
  response_body: string | null;
  attempt_count: number;
  status: "pending" | "delivered" | "failed";
  delivered_at: string | null;
  created_at: string;
}

export interface RecentActivityItem {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  actor_type: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface ApiRequestLogItem {
  id: string;
  api_key_id: string | null;
  request_id: string;
  method: string;
  path: string;
  status_code: number;
  pages_processed: number;
  latency_ms: number;
  created_at: string;
}

export interface UsageSummary {
  total_invoices: number;
  queued_invoices: number;
  processing_invoices: number;
  extracted_invoices: number;
  needs_review_invoices: number;
  approved_invoices: number;
  rejected_invoices: number;
  failed_invoices: number;
  total_pages_processed: number;
  api_requests_30d: number;
  recent_activity: RecentActivityItem[];
  recent_api_requests: ApiRequestLogItem[];
}
