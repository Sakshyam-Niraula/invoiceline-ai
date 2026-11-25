from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from app.db.base import Database

Severity = Literal["passed", "warning", "failed", "requires_review"]
ValidationSummaryStatus = Literal["passed", "warning", "failed", "requires_review"]

VALID_CURRENCIES = {
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "NZD",
    "CHF",
    "JPY",
    "CNY",
    "INR",
    "SGD",
    "HKD",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "MXN",
    "BRL",
    "ZAR",
    "AED",
    "SAR",
}

STRICT_TOLERANCE = Decimal("0.02")
ROUNDING_TOLERANCE = Decimal("0.05")
LOW_CONFIDENCE_THRESHOLD = 0.80


@dataclass(frozen=True)
class ValidationFinding:
    rule_code: str
    severity: Severity
    message: str
    field_path: str | None = None
    line_index: int | None = None
    expected_value: str | None = None
    actual_value: str | None = None


def _parse_decimal(val: Any) -> Decimal | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _q2(val: Decimal) -> str:
    return str(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_iso_date(val: Any) -> date | None:
    if not val:
        return None
    s = str(val).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


async def validate_invoice_data(
    db: Database,
    *,
    organisation_id: str,
    invoice_id: str,
    header: dict[str, Any],
    line_items: list[dict[str, Any]],
    field_metadata: dict[str, Any] | None = None,
) -> tuple[ValidationSummaryStatus, list[ValidationFinding]]:
    """Run deterministic financial and structural validation rules on an invoice."""
    findings: list[ValidationFinding] = []

    # 1. Missing required header fields
    required_fields = [
        ("supplier_name", "Supplier name"),
        ("invoice_number", "Invoice number"),
        ("invoice_date", "Invoice date"),
        ("currency", "Currency"),
        ("total_amount", "Total amount"),
    ]
    for field_key, label in required_fields:
        val = header.get(field_key)
        if val is None or not str(val).strip():
            findings.append(
                ValidationFinding(
                    rule_code="missing_required_field",
                    severity="failed",
                    field_path=field_key,
                    message=f"{label} is required but missing.",
                )
            )

    # 2. Currency consistency
    currency = (str(header.get("currency") or "").strip()).upper()
    if currency:
        if len(currency) != 3 or not currency.isalpha():
            findings.append(
                ValidationFinding(
                    rule_code="invalid_currency",
                    severity="failed",
                    field_path="currency",
                    message=f"Currency '{currency}' must be a 3-letter ISO-4217 code.",
                    actual_value=currency,
                )
            )
        elif currency not in VALID_CURRENCIES:
            findings.append(
                ValidationFinding(
                    rule_code="uncommon_currency",
                    severity="warning",
                    field_path="currency",
                    message=f"Currency '{currency}' is outside common ISO-4217 currencies; verify.",
                    actual_value=currency,
                )
            )

    # 3. Date validity and ordering
    inv_date_raw = header.get("invoice_date")
    due_date_raw = header.get("due_date")
    inv_date = _parse_iso_date(inv_date_raw)
    due_date = _parse_iso_date(due_date_raw)

    if inv_date_raw and inv_date is None:
        findings.append(
            ValidationFinding(
                rule_code="invalid_invoice_date",
                severity="failed",
                field_path="invoice_date",
                message="Invoice date could not be parsed as ISO YYYY-MM-DD.",
                actual_value=str(inv_date_raw),
            )
        )
    if due_date_raw and due_date is None:
        findings.append(
            ValidationFinding(
                rule_code="invalid_due_date",
                severity="failed",
                field_path="due_date",
                message="Due date could not be parsed as ISO YYYY-MM-DD.",
                actual_value=str(due_date_raw),
            )
        )
    if inv_date is not None and due_date is not None and due_date < inv_date:
        findings.append(
            ValidationFinding(
                rule_code="due_before_invoice_date",
                severity="failed",
                field_path="due_date",
                message="Due date is earlier than the invoice date.",
                expected_value=f">= {inv_date.isoformat()}",
                actual_value=due_date.isoformat(),
            )
        )

    # 4. Duplicate invoice number within organisation
    supplier_name = str(header.get("supplier_name") or "").strip()
    invoice_number = str(header.get("invoice_number") or "").strip()
    if invoice_number:
        dup_row = await db.fetchone(
            """
            SELECT id, supplier_name FROM invoices
            WHERE organisation_id = ? AND id != ? AND deleted_at IS NULL
              AND LOWER(TRIM(invoice_number)) = LOWER(?)
              AND (? = '' OR LOWER(TRIM(COALESCE(supplier_name, ''))) = LOWER(?))
            LIMIT 1
            """,
            (organisation_id, invoice_id, invoice_number, supplier_name, supplier_name),
        )
        if dup_row is not None:
            findings.append(
                ValidationFinding(
                    rule_code="duplicate_invoice_number",
                    severity="requires_review",
                    field_path="invoice_number",
                    message=(
                        f"Duplicate invoice number '{invoice_number}' found in existing "
                        f"record {dup_row['id']}."
                    ),
                    actual_value=invoice_number,
                )
            )

    # 5. Line-item arithmetic and tax consistency
    total_amount = _parse_decimal(header.get("total_amount"))
    subtotal = _parse_decimal(header.get("subtotal"))
    tax_total = _parse_decimal(header.get("tax_total"))
    discount_total = _parse_decimal(header.get("discount_total")) or Decimal("0")

    if header.get("total_amount") is not None and total_amount is None:
        findings.append(
            ValidationFinding(
                rule_code="invalid_total_amount",
                severity="failed",
                field_path="total_amount",
                message="Total amount is not a valid decimal number.",
                actual_value=str(header.get("total_amount")),
            )
        )

    if not line_items and total_amount is not None and total_amount > Decimal("0"):
        findings.append(
            ValidationFinding(
                rule_code="missing_line_items",
                severity="requires_review",
                field_path="line_items",
                message="No line items were extracted for a non-zero invoice total.",
            )
        )

    computed_net_sum = Decimal("0")
    computed_tax_sum = Decimal("0")
    lines_with_net = 0
    lines_with_tax = 0

    for idx, item in enumerate(line_items):
        desc = str(item.get("description") or "").strip()
        if not desc:
            findings.append(
                ValidationFinding(
                    rule_code="empty_line_description",
                    severity="warning",
                    field_path=f"line_items[{idx}].description",
                    line_index=idx,
                    message=f"Line {idx + 1} is missing a description.",
                )
            )

        qty = _parse_decimal(item.get("quantity"))
        unit_price = _parse_decimal(item.get("unit_price"))
        discount = _parse_decimal(item.get("discount")) or Decimal("0")
        tax_rate = _parse_decimal(item.get("tax_rate"))
        tax_amt = _parse_decimal(item.get("tax_amount"))
        line_total = _parse_decimal(item.get("line_total"))

        if qty is not None and qty < Decimal("0"):
            findings.append(
                ValidationFinding(
                    rule_code="negative_quantity",
                    severity="warning",
                    field_path=f"line_items[{idx}].quantity",
                    line_index=idx,
                    message=f"Line {idx + 1} has a negative quantity ({qty}); verify credit.",
                    actual_value=str(qty),
                )
            )

        expected_net: Decimal | None = None
        if qty is not None and unit_price is not None:
            expected_net = (qty * unit_price) - discount
            if line_total is not None:
                diff_net = abs(expected_net - line_total)
                diff_gross = (
                    abs((expected_net + tax_amt) - line_total)
                    if tax_amt is not None
                    else diff_net
                )
                best_diff = min(diff_net, diff_gross)
                if best_diff > ROUNDING_TOLERANCE:
                    findings.append(
                        ValidationFinding(
                            rule_code="line_item_arithmetic_mismatch",
                            severity="failed",
                            field_path=f"line_items[{idx}].line_total",
                            line_index=idx,
                            message=(
                                f"Line {idx + 1} total does not equal quantity * unit_price"
                                f"{' - discount' if discount else ''}."
                            ),
                            expected_value=_q2(expected_net),
                            actual_value=_q2(line_total),
                        )
                    )
                elif best_diff > STRICT_TOLERANCE:
                    findings.append(
                        ValidationFinding(
                            rule_code="line_item_rounding_variance",
                            severity="warning",
                            field_path=f"line_items[{idx}].line_total",
                            line_index=idx,
                            message=f"Line {idx + 1} has minor rounding variance ({_q2(best_diff)}).",
                            expected_value=_q2(expected_net),
                            actual_value=_q2(line_total),
                        )
                    )

        # Accumulate net line total for subtotal comparison
        if expected_net is not None:
            computed_net_sum += expected_net
            lines_with_net += 1
        elif line_total is not None:
            computed_net_sum += line_total - (tax_amt or Decimal("0"))
            lines_with_net += 1

        # Line tax validation
        tax_base = expected_net if expected_net is not None else line_total
        if tax_rate is not None and tax_amt is not None and tax_base is not None:
            expected_line_tax = tax_base * (tax_rate / Decimal("100"))
            tax_diff = abs(expected_line_tax - tax_amt)
            if tax_diff > ROUNDING_TOLERANCE:
                findings.append(
                    ValidationFinding(
                        rule_code="line_tax_mismatch",
                        severity="failed",
                        field_path=f"line_items[{idx}].tax_amount",
                        line_index=idx,
                        message=f"Line {idx + 1} tax amount disagrees with {tax_rate}% tax rate.",
                        expected_value=_q2(expected_line_tax),
                        actual_value=_q2(tax_amt),
                    )
                )

        if tax_amt is not None:
            computed_tax_sum += tax_amt
            lines_with_tax += 1

        conf = item.get("confidence")
        if conf is not None and float(conf) < LOW_CONFIDENCE_THRESHOLD:
            findings.append(
                ValidationFinding(
                    rule_code="low_confidence_line_item",
                    severity="requires_review",
                    field_path=f"line_items[{idx}]",
                    line_index=idx,
                    message=f"Line {idx + 1} extraction confidence is {float(conf):.0%}.",
                    actual_value=f"{float(conf):.2f}",
                )
            )

    # 6. Subtotal vs sum of line items
    if line_items and lines_with_net == len(line_items):
        if subtotal is not None:
            sub_diff = abs(computed_net_sum - subtotal)
            if sub_diff > ROUNDING_TOLERANCE:
                findings.append(
                    ValidationFinding(
                        rule_code="subtotal_mismatch",
                        severity="failed",
                        field_path="subtotal",
                        message="Invoice subtotal does not match sum of line items.",
                        expected_value=_q2(computed_net_sum),
                        actual_value=_q2(subtotal),
                    )
                )
            elif sub_diff > STRICT_TOLERANCE:
                findings.append(
                    ValidationFinding(
                        rule_code="subtotal_rounding_variance",
                        severity="warning",
                        field_path="subtotal",
                        message="Invoice subtotal has minor rounding difference vs line items.",
                        expected_value=_q2(computed_net_sum),
                        actual_value=_q2(subtotal),
                    )
                )
        elif total_amount is not None:
            # No explicit subtotal extracted; verify line sum + tax - discount == total
            inferred_total = computed_net_sum + (tax_total or Decimal("0")) - discount_total
            if abs(inferred_total - total_amount) > ROUNDING_TOLERANCE:
                findings.append(
                    ValidationFinding(
                        rule_code="inferred_subtotal_mismatch",
                        severity="warning",
                        field_path="total_amount",
                        message="Line item sum + tax does not reconcile with total amount.",
                        expected_value=_q2(inferred_total),
                        actual_value=_q2(total_amount),
                    )
                )

    # 7. Tax total vs sum of line taxes
    if tax_total is not None and lines_with_tax > 0 and lines_with_tax == len(line_items):
        tax_sum_diff = abs(computed_tax_sum - tax_total)
        if tax_sum_diff > ROUNDING_TOLERANCE:
            findings.append(
                ValidationFinding(
                    rule_code="tax_total_mismatch",
                    severity="failed",
                    field_path="tax_total",
                    message="Invoice tax total does not match sum of line-item taxes.",
                    expected_value=_q2(computed_tax_sum),
                    actual_value=_q2(tax_total),
                )
            )

    # 8. Invoice grand total reconciliation
    if subtotal is not None and total_amount is not None:
        expected_total = subtotal + (tax_total or Decimal("0")) - discount_total
        total_diff = abs(expected_total - total_amount)
        if total_diff > ROUNDING_TOLERANCE:
            findings.append(
                ValidationFinding(
                    rule_code="invoice_total_mismatch",
                    severity="failed",
                    field_path="total_amount",
                    message="Total amount does not equal subtotal + tax - discount.",
                    expected_value=_q2(expected_total),
                    actual_value=_q2(total_amount),
                )
            )
        elif total_diff > STRICT_TOLERANCE:
            findings.append(
                ValidationFinding(
                    rule_code="invoice_total_rounding_variance",
                    severity="warning",
                    field_path="total_amount",
                    message="Invoice total has minor rounding variance vs subtotal + tax.",
                    expected_value=_q2(expected_total),
                    actual_value=_q2(total_amount),
                )
            )

    # 9. Low confidence header fields
    if field_metadata:
        for field_key, meta in field_metadata.items():
            if not isinstance(meta, dict):
                continue
            conf = meta.get("confidence")
            if conf is not None and float(conf) < LOW_CONFIDENCE_THRESHOLD:
                findings.append(
                    ValidationFinding(
                        rule_code="low_confidence_field",
                        severity="requires_review",
                        field_path=field_key,
                        message=f"Field '{field_key}' has low extraction confidence ({float(conf):.0%}).",
                        actual_value=f"{float(conf):.2f}",
                    )
                )

    if not findings:
        findings.append(
            ValidationFinding(
                rule_code="all_checks_passed",
                severity="passed",
                message="All deterministic arithmetic, date, currency, and required-field checks passed.",
            )
        )
        return "passed", findings

    severities = {f.severity for f in findings}
    if "failed" in severities:
        return "failed", findings
    if "requires_review" in severities:
        return "requires_review", findings
    if "warning" in severities:
        return "warning", findings
    return "passed", findings
