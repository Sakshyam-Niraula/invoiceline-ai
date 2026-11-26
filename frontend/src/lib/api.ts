import type {
  ApiKeyItem,
  AuthSession,
  CreateApiKeyResponse,
  CreateWebhookResponse,
  InvoiceDetail,
  InvoiceListResponse,
  InvoiceUploadResponse,
  MeResponse,
  Member,
  OrganisationSummary,
  RoleType,
  UsageSummary,
  WebhookDelivery,
  WebhookItem,
} from "@/types/api";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

const TOKEN_STORAGE_KEY = "invoiceline_access_token";
const REFRESH_STORAGE_KEY = "invoiceline_refresh_token";
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || "";
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || "";

export class ApiClientError extends Error {
  status: number;
  code: string;
  requestId?: string;
  details?: unknown;

  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    window.sessionStorage.removeItem(REFRESH_STORAGE_KEY);
  }
}

export function setStoredRefreshToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.sessionStorage.setItem(REFRESH_STORAGE_KEY, token);
  else window.sessionStorage.removeItem(REFRESH_STORAGE_KEY);
}

async function supabaseAuth(path: string, body: Record<string, unknown>): Promise<any> {
  if (!SUPABASE_URL || !SUPABASE_KEY) throw new Error("Supabase Auth is not configured in this deployment.");
  const response = await fetch(`${SUPABASE_URL}/auth/v1/${path}`, {
    method: "POST",
    headers: { apikey: SUPABASE_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.msg || payload.message || payload.error_description || payload.error || "Authentication failed.");
  return payload;
}

export async function signUpWithSupabase(input: { email: string; password: string; full_name: string; organisation_name: string }) {
  return supabaseAuth("signup", { email: input.email, password: input.password, data: { full_name: input.full_name, organisation_name: input.organisation_name } });
}

export async function signInWithSupabase(email: string, password: string) {
  return supabaseAuth("token?grant_type=password", { email, password });
}

export async function provisionSupabaseSession(accessToken: string): Promise<AuthSession> {
  const response = await fetch(`${API_BASE}/v1/auth/provision`, { method: "POST", headers: { Authorization: `Bearer ${accessToken}` } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || `Account setup failed (${response.status}).`);
  return payload as AuthSession;
}

async function refreshSupabaseAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const refreshToken = window.sessionStorage.getItem(REFRESH_STORAGE_KEY);
  if (!refreshToken || !SUPABASE_URL || !SUPABASE_KEY) return null;
  try {
    const payload = await supabaseAuth("token?grant_type=refresh_token", { refresh_token: refreshToken });
    setStoredToken(payload.access_token);
    setStoredRefreshToken(payload.refresh_token);
    return payload.access_token as string;
  } catch { setStoredToken(null); return null; }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body && typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let resp = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  if (resp.status === 401 && token) {
    const refreshed = await refreshSupabaseAccessToken();
    if (refreshed) {
      headers.set("Authorization", `Bearer ${refreshed}`);
      resp = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
    }
  }

  if (resp.status === 204) {
    return undefined as T;
  }

  const contentType = resp.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await resp.json() : await resp.text();

  if (!resp.ok) {
    const errObj = typeof payload === "object" && payload && "error" in payload ? payload.error : null;
    throw new ApiClientError(
      resp.status,
      errObj?.code || "request_failed",
      errObj?.message || `Request failed with status ${resp.status}`,
      errObj?.request_id,
      errObj?.details,
    );
  }

  return payload as T;
}

export const api = {
  getApiBase: () => API_BASE,

  register: (body: {
    email: string;
    password: string;
    full_name: string;
    organisation_name: string;
    turnstile_token?: string;
  }) => apiFetch<AuthSession>("/v1/auth/register", { method: "POST", body: JSON.stringify(body) }),

  login: (body: { email: string; password: string; turnstile_token?: string }) =>
    apiFetch<AuthSession>("/v1/auth/login", { method: "POST", body: JSON.stringify(body) }),

  logout: async () => {
    const token = getStoredToken();
    if (token && SUPABASE_URL && SUPABASE_KEY) {
      try {
        await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
          method: "POST",
          headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${token}` },
        });
      } catch {}
    }
    setStoredToken(null);
  },

  me: () => apiFetch<MeResponse>("/v1/auth/me"),

  requestPasswordReset: (body: { email: string; turnstile_token?: string }) =>
    apiFetch<{ message: string; dev_reset_token?: string }>("/v1/auth/password-reset/request", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  confirmPasswordReset: (body: { token: string; new_password: string }) =>
    apiFetch<{ message: string }>("/v1/auth/password-reset/confirm", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getUsage: () => apiFetch<UsageSummary>("/v1/usage"),

  listInvoices: (params: {
    q?: string;
    status?: string;
    validation_status?: string;
    sort_by?: string;
    sort_dir?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    });
    return apiFetch<InvoiceListResponse>(`/v1/invoices?${qs.toString()}`);
  },

  getInvoice: (id: string) => apiFetch<InvoiceDetail>(`/v1/invoices/${id}`),

  validateInvoice: (id: string) =>
    apiFetch<InvoiceDetail>(`/v1/invoices/${id}/validate`, { method: "POST" }),

  reviewInvoice: (id: string, body: Record<string, unknown>) =>
    apiFetch<InvoiceDetail>(`/v1/invoices/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteInvoice: (id: string) => apiFetch<void>(`/v1/invoices/${id}`, { method: "DELETE" }),

  exportInvoiceUrl: (id: string, format: "csv" | "json") =>
    `${API_BASE}/v1/invoices/${id}/export?format=${format}`,

  downloadExport: async (id: string, format: "csv" | "json") => {
    const token = getStoredToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const resp = await fetch(`${API_BASE}/v1/invoices/${id}/export?format=${format}`, {
      headers,
      credentials: "include",
    });
    if (!resp.ok) {
      throw new ApiClientError(resp.status, "export_failed", "Failed to export invoice");
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${id}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  uploadInvoicesWithProgress: (
    files: File[],
    onProgress: (percent: number) => void,
  ): Promise<InvoiceUploadResponse> =>
    new Promise((resolve, reject) => {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/v1/invoices`);
      xhr.withCredentials = true;
      const token = getStoredToken();
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }
      xhr.upload.onprogress = (evt) => {
        if (evt.lengthComputable) {
          onProgress(Math.round((evt.loaded / evt.total) * 100));
        }
      };
      xhr.onload = () => {
        try {
          const parsed = JSON.parse(xhr.responseText || "{}");
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(parsed as InvoiceUploadResponse);
          } else {
            reject(
              new ApiClientError(
                xhr.status,
                parsed?.error?.code || "upload_failed",
                parsed?.error?.message || "Upload failed",
                parsed?.error?.request_id,
              ),
            );
          }
        } catch {
          reject(new ApiClientError(xhr.status, "upload_failed", "Upload failed"));
        }
      };
      xhr.onerror = () => {
        reject(new ApiClientError(0, "network_error", "Network error while uploading files"));
      };
      xhr.send(formData);
    }),

  listApiKeys: () => apiFetch<ApiKeyItem[]>("/v1/api-keys"),

  createApiKey: (body: { name: string; scopes: string[] }) =>
    apiFetch<CreateApiKeyResponse>("/v1/api-keys", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  revokeApiKey: (id: string) => apiFetch<void>(`/v1/api-keys/${id}`, { method: "DELETE" }),

  listWebhooks: () => apiFetch<WebhookItem[]>("/v1/webhooks"),

  createWebhook: (body: { url: string; events: string[] }) =>
    apiFetch<CreateWebhookResponse>("/v1/webhooks", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteWebhook: (id: string) => apiFetch<void>(`/v1/webhooks/${id}`, { method: "DELETE" }),

  listWebhookDeliveries: () => apiFetch<WebhookDelivery[]>("/v1/webhooks/deliveries"),

  getOrganisation: () => apiFetch<OrganisationSummary>("/v1/organisation"),

  updateOrganisation: (body: { name?: string; retention_days?: number }) =>
    apiFetch<OrganisationSummary>("/v1/organisation", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  listMembers: () => apiFetch<Member[]>("/v1/organisation/members"),

  addMember: (body: { email: string; full_name: string; password: string; role: RoleType }) =>
    apiFetch<Member>("/v1/organisation/members", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateMemberRole: (membershipId: string, role: RoleType) =>
    apiFetch<Member>(`/v1/organisation/members/${membershipId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),

  removeMember: (membershipId: string) =>
    apiFetch<void>(`/v1/organisation/members/${membershipId}`, { method: "DELETE" }),
};
