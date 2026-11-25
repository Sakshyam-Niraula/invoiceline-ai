from __future__ import annotations

import hashlib
import hmac

import httpx

from app.core.errors import ExternalProviderError
from app.services.extraction.base import (
    ExtractedFieldMeta,
    ExtractedInvoiceData,
    ExtractedLineItem,
)
from tests.conftest import StubExtractionProvider

SAMPLE_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n2 0 obj\n<< /Type /Page >>\nendobj\n%%EOF"
SAMPLE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"


async def _register(client: httpx.AsyncClient, email: str, org: str) -> str:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ngPass!23",
            "full_name": "Finance Lead",
            "organisation_name": org,
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


async def test_upload_validation_idempotency_and_duplicates(
    client: httpx.AsyncClient,
    stub_provider: StubExtractionProvider,
) -> None:
    token = await _register(client, "ops@northstar.test", "Northstar")
    headers = {"Authorization": f"Bearer {token}"}

    # Reject bad magic bytes disguised as PDF
    bad = await client.post(
        "/v1/invoices",
        headers=headers,
        files={"file": ("fake.pdf", b"not-a-real-pdf", "application/pdf")},
    )
    assert bad.status_code == 415

    stub_provider.next_result = ExtractedInvoiceData(
        provider_name="gemini",
        page_count=2,
        supplier_name="Cloudflare Inc",
        invoice_number="INV-1001",
        invoice_date="2026-10-01",
        due_date="2026-10-31",
        currency="USD",
        subtotal="250.00",
        tax_total="25.00",
        total_amount="275.00",
        field_metadata={
            "supplier_name": ExtractedFieldMeta(0.98, "Page 1, header"),
            "invoice_number": ExtractedFieldMeta(0.99, "Page 1, top right"),
        },
        line_items=[
            ExtractedLineItem(
                line_index=0,
                page_number=1,
                description="Workers Pro Plan",
                quantity="2",
                unit="seat",
                unit_price="100.00",
                tax_rate="10",
                tax_amount="20.00",
                line_total="200.00",
                confidence=0.97,
                source_reference="Page 1, row 1",
            ),
            ExtractedLineItem(
                line_index=1,
                page_number=2,
                description="R2 Storage Add-on",
                quantity="1",
                unit="month",
                unit_price="50.00",
                tax_rate="10",
                tax_amount="5.00",
                line_total="50.00",
                confidence=0.95,
                source_reference="Page 2, row 1",
            ),
        ],
        raw_response={"ok": True},
    )

    # Upload with Idempotency-Key
    up1 = await client.post(
        "/v1/invoices",
        headers={**headers, "Idempotency-Key": "idem-abc-1"},
        files={"file": ("cf-inv.pdf", SAMPLE_PDF, "application/pdf")},
    )
    assert up1.status_code == 201
    inv1 = up1.json()["invoices"][0]

    # Retry with same Idempotency-Key returns same invoice ID
    up1_retry = await client.post(
        "/v1/invoices",
        headers={**headers, "Idempotency-Key": "idem-abc-1"},
        files={"file": ("cf-inv.pdf", SAMPLE_PDF, "application/pdf")},
    )
    assert up1_retry.status_code == 201
    assert up1_retry.json()["invoices"][0]["id"] == inv1["id"]

    # Second distinct upload with same PDF content triggers duplicate_file_detected + duplicate_invoice_number
    up2 = await client.post(
        "/v1/invoices",
        headers=headers,
        files={"file": ("cf-inv-copy.pdf", SAMPLE_PDF, "application/pdf")},
    )
    assert up2.status_code == 201
    inv2 = up2.json()["invoices"][0]
    assert inv2["id"] != inv1["id"]
    assert inv2["duplicate_file_detected"] is True

    detail2 = await client.get(f"/v1/invoices/{inv2['id']}", headers=headers)
    assert detail2.status_code == 200
    assert detail2.json()["validation_status"] == "requires_review"
    rules = {r["rule_code"] for r in detail2.json()["validation_results"]}
    assert "duplicate_invoice_number" in rules


async def test_validation_arithmetic_correction_and_approval_gate(
    client: httpx.AsyncClient,
    stub_provider: StubExtractionProvider,
) -> None:
    token = await _register(client, "ap@ledger.test", "Ledger Co")
    headers = {"Authorization": f"Bearer {token}"}

    # Provider emits arithmetic errors: 3 * 40 = 120, but line_total=150 and total_amount=200
    stub_provider.next_result = ExtractedInvoiceData(
        provider_name="gemini",
        page_count=1,
        supplier_name="Acme Supplies",
        invoice_number="INV-900",
        invoice_date="2026-11-10",
        due_date="2026-11-01",  # invalid: due before invoice date
        currency="USD",
        subtotal="120.00",
        tax_total="12.00",
        total_amount="200.00",
        line_items=[
            ExtractedLineItem(
                line_index=0,
                page_number=1,
                description="Paper Reams",
                quantity="3",
                unit_price="40.00",
                tax_rate="10",
                tax_amount="12.00",
                line_total="150.00",
                confidence=0.92,
            )
        ],
    )

    up = await client.post(
        "/v1/invoices",
        headers=headers,
        files={"file": ("receipt.png", SAMPLE_PNG, "image/png")},
    )
    assert up.status_code == 201
    inv_id = up.json()["invoices"][0]["id"]

    detail = await client.get(f"/v1/invoices/{inv_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "needs_review"
    assert body["validation_status"] == "failed"
    codes = {r["rule_code"] for r in body["validation_results"]}
    assert "due_before_invoice_date" in codes
    assert "line_item_arithmetic_mismatch" in codes
    assert "invoice_total_mismatch" in codes

    # Attempting to approve with failed validation is rejected
    bad_approve = await client.post(
        f"/v1/invoices/{inv_id}/review",
        headers=headers,
        json={"action": "approve"},
    )
    assert bad_approve.status_code == 400

    # Fix due_date, line_total, and total_amount, then approve
    fixed_approve = await client.post(
        f"/v1/invoices/{inv_id}/review",
        headers=headers,
        json={
            "action": "approve",
            "due_date": "2026-12-10",
            "subtotal": "120.00",
            "tax_total": "12.00",
            "total_amount": "132.00",
            "review_notes": "Corrected line total and due date",
            "line_items": [
                {
                    "page_number": 1,
                    "description": "Paper Reams",
                    "quantity": "3",
                    "unit_price": "40.00",
                    "tax_rate": "10",
                    "tax_amount": "12.00",
                    "line_total": "120.00",
                }
            ],
        },
    )
    assert fixed_approve.status_code == 200
    approved = fixed_approve.json()
    assert approved["status"] == "approved"
    assert approved["validation_status"] == "passed"

    # CSV export works
    csv_resp = await client.get(f"/v1/invoices/{inv_id}/export?format=csv", headers=headers)
    assert csv_resp.status_code == 200
    assert "Paper Reams" in csv_resp.text

    # Signed document URL works without Authorization header, but tampered URL fails
    doc_url = approved["document_url"]
    path_and_query = doc_url.replace("http://testserver", "")
    doc_resp = await client.get(path_and_query)
    assert doc_resp.status_code == 200
    assert doc_resp.content == SAMPLE_PNG

    tampered = await client.get(path_and_query + "x")
    assert tampered.status_code == 401


async def test_extraction_failure_usage_and_webhook_signature(
    client: httpx.AsyncClient,
    stub_provider: StubExtractionProvider,
    db,
    test_settings,
) -> None:
    token = await _register(client, "dev@platform.test", "Platform Inc")
    headers = {"Authorization": f"Bearer {token}"}

    # Create API key and webhook
    key_resp = await client.post(
        "/v1/api-keys",
        headers=headers,
        json={"name": "CI Key", "scopes": ["invoices:read", "invoices:write"]},
    )
    assert key_resp.status_code == 201
    api_key = key_resp.json()["secret_key"]
    api_headers = {"Authorization": f"Bearer {api_key}"}

    wh_resp = await client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": "https://webhook.example.test/hook", "events": ["invoice.failed"]},
    )
    assert wh_resp.status_code == 201
    wh_secret = wh_resp.json()["signing_secret"]

    # Simulate extraction provider error
    stub_provider.next_error = ExternalProviderError("Upstream 503 rate limit")
    fail_up = await client.post(
        "/v1/invoices",
        headers=api_headers,
        files={"file": ("broken.pdf", SAMPLE_PDF, "application/pdf")},
    )
    assert fail_up.status_code == 201
    inv_id = fail_up.json()["invoices"][0]["id"]

    status_resp = await client.get(f"/v1/invoices/{inv_id}/status", headers=api_headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "failed"
    assert "503" in status_resp.json()["extraction_error"]

    # Usage endpoint reflects real counts & API request log
    usage = await client.get("/v1/usage", headers=headers)
    assert usage.status_code == 200
    usage_data = usage.json()
    assert usage_data["total_invoices"] == 1
    assert usage_data["failed_invoices"] == 1
    assert usage_data["api_requests_30d"] >= 2

    # Verify webhook signature helper produces valid HMAC-SHA256
    from app.core.crypto import sign_webhook_payload

    sig = sign_webhook_payload(wh_secret, 1700000000, b'{"test":true}')
    parts = dict(p.split("=", 1) for p in sig.split(","))
    expected = hmac.new(
        wh_secret.encode("utf-8"),
        b'1700000000.{"test":true}',
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(parts["v1"], expected)

    # Cross-organisation access is blocked
    other_token = await _register(client, "intruder@other.test", "Other Org")
    cross = await client.get(
        f"/v1/invoices/{inv_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert cross.status_code == 404
