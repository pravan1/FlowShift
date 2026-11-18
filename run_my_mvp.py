#!/usr/bin/env python3
"""FlowShift six-use-case MVP demonstration using synthetic data only.

Run with: python3 run_my_mvp.py

Standard-library only. Money is integer USD cents. This is a local simulation:
it has no bank connection and cannot submit or move payments.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


MODE = "SYNTHETIC DATA · SIMULATION ONLY"
BUSINESS = "Demo business checking"
RESERVE_CENTS = 200_000


def money(cents: int) -> str:
    """Format integer cents as USD without using floating point arithmetic."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def require_nonnegative_cents(value: int, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be integer cents")
    if value < 0:
        raise ValueError(f"{field} must be nonnegative")


# All dates and figures below come from the handoff's synthetic arithmetic fixture.
ACCOUNT = {
    "id": "demo-checking-001",
    "name": BUSINESS,
    "currency": "USD",
    "available_cents": 840_000,
    "as_of": "2026-04-20T08:00:00-05:00",
    "mode": MODE,
}

OBLIGATIONS = [
    {"id": "supplier", "name": "Northstar Supply", "amount_cents": 180_000, "date": "2026-04-21", "payee_id": "northstar", "critical": False, "movable": True},
    {"id": "rent-insurance", "name": "Rent + insurance", "amount_cents": 288_800, "date": "2026-04-22", "payee_id": "landlord", "critical": True, "movable": False},
    {"id": "payroll", "name": "Payroll funding", "amount_cents": 400_000, "date": "2026-04-23", "payee_id": "payroll-provider", "critical": True, "movable": False},
]

RULES = {
    "version": 1,
    "reserve_cents": RESERVE_CENTS,
    "amount_ceiling_cents": 500_000,
    "strict_reserve": True,
    "protect_payroll": True,
    "require_approval_for_new_payee": True,
    "allowed_payees": {"landlord", "northstar", "payroll-provider"},
    "routine_notifications": True,
}


def cash_path(starting_cents: int, dated_events: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Return chronological end-of-day balances for signed cash events."""
    balance = starting_cents
    result = [{"date": "2026-04-20", "label": "Opening available balance", "delta_cents": 0, "balance_cents": balance}]
    for date, label, delta in sorted(dated_events, key=lambda row: (row[0], row[1])):
        balance += delta
        result.append({"date": date, "label": label, "delta_cents": delta, "balance_cents": balance})
    return result


def calculate_headroom(checkpoints: list[dict[str, Any]], reserve_cents: int) -> dict[str, int]:
    """Safe-to-spend is minimum conservative checkpoint headroom after reserve."""
    require_nonnegative_cents(reserve_cents, "reserve")
    if not checkpoints:
        raise ValueError("Cannot calculate headroom without balance checkpoints")
    headroom = min(row["balance_cents"] - reserve_cents for row in checkpoints)
    return {"safe_to_spend_cents": max(0, headroom), "reserve_gap_cents": max(0, -headroom), "minimum_headroom_cents": headroom}


def build_daily_cash_plan() -> dict[str, Any]:
    """UC-04: calculate baseline headroom and assemble the actionable feed."""
    # Baseline: supplier Tuesday, rent/insurance Wednesday, payroll Thursday,
    # settlement Friday; diagnostic overdraft fee is included Thursday.
    baseline_events = [
        ("2026-04-21", "Northstar Supply", -180_000),
        ("2026-04-22", "Rent + insurance", -288_800),
        ("2026-04-23", "Payroll funding", -400_000),
        ("2026-04-23", "Modeled baseline overdraft fee", -3_500),
        ("2026-04-24", "Customer settlement", 314_000),
    ]
    checkpoints = cash_path(ACCOUNT["available_cents"], baseline_events)
    # Checkpoint values include the balance after each event, so the lowest
    # within-day value is represented even where several events share a date.
    metrics = calculate_headroom(checkpoints, RULES["reserve_cents"])
    feed = [
        {"id": "payroll-risk", "kind": "alert", "title": "Payroll funding needs review", "source": "forecast/payroll", "action_needed": True},
        {"id": "supplier-timing", "kind": "suggestion", "title": "Review moving supplier payment to Friday", "source": "plan/supplier", "action_needed": True},
        {"id": "receipt-april", "kind": "savings", "title": "April simulated receipt is ready", "source": "receipt/2026-04", "action_needed": False},
    ]
    return {
        "account": ACCOUNT["name"], "mode": ACCOUNT["mode"], "as_of": ACCOUNT["as_of"],
        "reserve_cents": RULES["reserve_cents"], "checkpoints": checkpoints,
        "safe_to_spend_cents": metrics["safe_to_spend_cents"],
        "reserve_gap_cents": metrics["reserve_gap_cents"],
        "unresolved_incident_count": 1,
        "feed": feed,
    }


def check_guardrails(action: dict[str, Any], rules: dict[str, Any], projected_low_cents: int) -> dict[str, Any]:
    """UC-13/14: inclusive fee-aware ceiling and hard payee/reserve checks."""
    amount = action["amount_cents"]
    fee = action["fee_cents"]
    require_nonnegative_cents(amount, "action amount")
    require_nonnegative_cents(fee, "action fee")
    total = amount + fee
    reasons: list[str] = []
    if total > rules["amount_ceiling_cents"]:
        reasons.append("amount plus fee exceeds the inclusive approval ceiling")
    if action["payee_id"] not in rules["allowed_payees"]:
        reasons.append("new or unapproved payee requires human approval")
    if projected_low_cents < rules["reserve_cents"]:
        reasons.append("projected balance breaches strict reserve")
    if action.get("kind") == "payroll" and not rules["protect_payroll"]:
        reasons.append("payroll protection must remain enabled")
    return {
        "action_id": action["id"], "amount_plus_fee_cents": total,
        "ceiling_cents": rules["amount_ceiling_cents"],
        "passes_ceiling": total <= rules["amount_ceiling_cents"],
        "payee_approved": action["payee_id"] in rules["allowed_payees"],
        "projected_low_cents": projected_low_cents,
        "passes_reserve": projected_low_cents >= rules["reserve_cents"],
        "preview_result": "WOULD PASS" if not reasons else "REQUIRES REVIEW / BLOCKED",
        "reasons": reasons,
    }


def validate_and_version_rules(current: dict[str, Any], reserve_cents: int, ceiling_cents: int) -> dict[str, Any]:
    """UC-13: validate edits and return a new rule version without mutating active rules."""
    require_nonnegative_cents(reserve_cents, "reserve")
    require_nonnegative_cents(ceiling_cents, "approval ceiling")
    return {**current, "version": current["version"] + 1, "reserve_cents": reserve_cents,
            "amount_ceiling_cents": ceiling_cents, "strict_reserve": True,
            "protect_payroll": True, "require_approval_for_new_payee": True}


def build_autopilot_preview() -> dict[str, Any]:
    """UC-14: show simulated historical decisions; never approves or submits actions."""
    actions = [
        {"id": "move-supplier", "kind": "bill", "payee_id": "northstar", "amount_cents": 180_000, "fee_cents": 0, "date": "2026-04-24"},
        {"id": "payroll", "kind": "payroll", "payee_id": "payroll-provider", "amount_cents": 400_000, "fee_cents": 0, "date": "2026-04-23"},
        {"id": "new-vendor", "kind": "bill", "payee_id": "new-vendor", "amount_cents": 50_000, "fee_cents": 100, "date": "2026-04-24"},
    ]
    low_balance_by_date = {"2026-04-23": 151_200, "2026-04-24": 283_316}
    rows = [check_guardrails(action, RULES, low_balance_by_date[action["date"]]) for action in actions]
    return {"label": "HISTORICAL SIMULATION · NOT PAID", "rules_version": RULES["version"],
            "evaluated_count": len(rows), "would_pass_count": sum(row["preview_result"] == "WOULD PASS" for row in rows),
            "requires_review_count": sum(row["preview_result"] != "WOULD PASS" for row in rows), "actions": rows}


@dataclass(frozen=True)
class ReceiptEntry:
    id: str
    period: str
    category: str
    amount_cents: int
    status: str
    source_action_id: str
    note: str


RECEIPT_ENTRIES = [
    ReceiptEntry("benefit-overdraft", "2026-04", "avoided_fee", 3_500, "simulated", "baseline-overdraft", "Counterfactual fee counted once."),
    ReceiptEntry("cost-acceleration", "2026-04", "execution_cost", 1_884, "simulated", "accelerated-settlement", "Synthetic 0.6% acceleration fee."),
    ReceiptEntry("discount-sample", "2026-04", "discount", 0, "estimated", "supplier-discount", "No confirmed discount included in this fixture."),
]


def calculate_savings_receipt(period: str, entries: list[ReceiptEntry] = RECEIPT_ENTRIES) -> dict[str, Any]:
    """UC-15: gross modeled benefits less incremental costs; no principal counted."""
    lines = [entry for entry in entries if entry.period == period]
    gross = sum(entry.amount_cents for entry in lines if entry.category in {"avoided_fee", "discount"})
    costs = sum(entry.amount_cents for entry in lines if entry.category == "execution_cost")
    return {"period": period, "label": "SIMULATED · NOT REALIZED CASH", "entries": lines,
            "gross_benefit_cents": gross, "incremental_cost_cents": costs,
            "net_savings_cents": gross - costs,
            "overdrafts_prevented_count": 1,
            "reserve_preserved_cents": 0}  # reserve preservation is deliberately not savings


def render_receipt_csv(receipt: dict[str, Any]) -> str:
    """Render a small CSV with spreadsheet-formula-safe text fields."""
    import csv
    import io

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["id", "period", "category", "amount_cents", "status", "source_action_id", "note"])
    for item in receipt["entries"]:
        note = item.note
        if note.startswith(("=", "+", "-", "@")):
            note = "'" + note
        writer.writerow([item.id, item.period, item.category, item.amount_cents, item.status, item.source_action_id, note])
    return buffer.getvalue().rstrip()


NOTIFICATION_PREFERENCES = {
    "routine_enabled": True,
    "business_timezone": "America/Chicago",
    "quiet_weekends": True,
    "hide_amounts_on_lock_screen": True,
}

NOTIFICATION_CANDIDATES = [
    {"id": "daily-summary-2026-04-20", "incident_key": "daily-summary:2026-04-20", "kind": "routine", "title": "Your daily cash summary", "body": "Review today's cash plan."},
    {"id": "payroll-risk-2026-04-23", "incident_key": "payroll-risk:payroll:2026-04-23", "kind": "critical", "title": "Payroll funding risk", "body": "A forecast shortfall needs attention."},
    {"id": "security-consent", "incident_key": "security:consent-expiring", "kind": "security", "title": "Account access needs attention", "body": "Reconnect through your bank to refresh account data."},
]


def layout_notifications(preferences: dict[str, Any], already_sent: set[str], weekday: int = 0) -> list[dict[str, Any]]:
    """UC-16: create in-app cards and delivery decisions with deduplication."""
    weekend_quiet = preferences["quiet_weekends"] and weekday in {5, 6}
    result = []
    for item in NOTIFICATION_CANDIDATES:
        if item["incident_key"] in already_sent:
            continue
        critical = item["kind"] in {"critical", "security"}
        push = critical or (preferences["routine_enabled"] and not weekend_quiet)
        result.append({
            **item,
            "in_app": True,
            "push": push,
            "preview": item["title"] if preferences["hide_amounts_on_lock_screen"] else item["body"],
            "group": "Cash risk" if item["kind"] == "critical" else ("Security" if item["kind"] == "security" else "Daily summary"),
        })
    return result


@dataclass(frozen=True)
class AuditEvent:
    id: str
    at: str
    actor: str
    actor_role: str
    event_type: str
    entity_id: str
    correlation_id: str
    environment: str
    details: dict[str, Any]


AUDIT_LOG: list[AuditEvent] = []


def append_audit(event: AuditEvent, log: list[AuditEvent] = AUDIT_LOG) -> None:
    """UC-19: append once by unique event ID; records are immutable dataclasses."""
    if any(existing.id == event.id for existing in log):
        return
    log.append(event)


def read_audit_history(actor_role: str, entity_id: str, log: list[AuditEvent] = AUDIT_LOG) -> list[AuditEvent]:
    """Demo role gate and entity filter. Production authorization must be server-side."""
    if actor_role not in {"owner", "operator"}:
        raise PermissionError("Only an authorized owner or operator may view audit history.")
    return sorted((event for event in log if event.entity_id == entity_id), key=lambda event: event.at)


def print_section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> None:
    print(f"FlowShift MVP mock demonstration — {MODE}")
    print("No banking network, external data, or payment submission is used.")

    print_section("UC-04 · DAILY CASH PLAN / HEADROOM")
    plan = build_daily_cash_plan()
    print(f"Account: {plan['account']} | as of {plan['as_of']}")
    print(f"Reserve: {money(plan['reserve_cents'])}")
    for point in plan["checkpoints"]:
        print(f"{point['date']}  {point['label']:<32} {money(point['balance_cents'])}")
    print(f"Safe to Spend: {money(plan['safe_to_spend_cents'])}")
    print(f"Reserve gap: {money(plan['reserve_gap_cents'])}; unresolved incidents: {plan['unresolved_incident_count']}")
    print("Feed:")
    for item in plan["feed"]:
        print(f"  [{item['kind'].upper()}] {item['title']} → {item['source']}")

    print_section("UC-13 · GUARDRAILS / VERSIONED RULES")
    print(f"Active rule version: {RULES['version']}")
    print(f"Approval ceiling (fee-inclusive): {money(RULES['amount_ceiling_cents'])}")
    print(f"Strict reserve: {RULES['strict_reserve']} | Payroll protected: {RULES['protect_payroll']}")
    revised_rules = validate_and_version_rules(RULES, reserve_cents=225_000, ceiling_cents=500_000)
    print(f"Example validated draft save → version {revised_rules['version']}, reserve {money(revised_rules['reserve_cents'])}")
    print(f"Original active rules remain version {RULES['version']} (no in-place mutation).")
    ceiling_demo = check_guardrails({"id": "ceiling-boundary", "payee_id": "northstar", "amount_cents": 499_000, "fee_cents": 1_000}, RULES, 250_000)
    print(f"Boundary example: $4990.00 + $10.00 fee = {money(ceiling_demo['amount_plus_fee_cents'])}; {ceiling_demo['preview_result']}")

    print_section("UC-14 · AUTOPILOT HISTORICAL PREVIEW")
    preview = build_autopilot_preview()
    print(f"{preview['label']} | rules version {preview['rules_version']}")
    print(f"Evaluated {preview['evaluated_count']} actions: {preview['would_pass_count']} pass, {preview['requires_review_count']} need review/block.")
    for row in preview["actions"]:
        print(f"  {row['action_id']}: {row['preview_result']} — {', '.join(row['reasons']) or 'all preview checks passed'}")

    print_section("UC-15 · SAVINGS RECEIPT")
    receipt = calculate_savings_receipt("2026-04")
    print(receipt["label"])
    for entry in receipt["entries"]:
        print(f"  {entry.category:<16} {money(entry.amount_cents):>10} [{entry.status}] — {entry.note}")
    print(f"Gross modeled benefit: {money(receipt['gross_benefit_cents'])}")
    print(f"Incremental costs:     {money(receipt['incremental_cost_cents'])}")
    print(f"Net modeled savings:   {money(receipt['net_savings_cents'])}")
    print(f"CSV preview: {render_receipt_csv(receipt).splitlines()[0]}")

    print_section("UC-16 · NOTIFICATIONS")
    notifications = layout_notifications(NOTIFICATION_PREFERENCES, already_sent=set(), weekday=0)
    print(f"Layout time: {datetime.fromisoformat(ACCOUNT['as_of']).strftime('%Y-%m-%d %H:%M %Z')} ({NOTIFICATION_PREFERENCES['business_timezone']})")
    for item in notifications:
        print(f"  [{item['group']}] {item['title']} | in-app={item['in_app']} push={item['push']} | lock-screen: {item['preview']}")

    print_section("UC-19 · AUDIT HISTORY")
    now = "2026-04-20T13:00:00Z"
    append_audit(AuditEvent("audit-001", now, "demo-owner", "owner", "rules.previewed", ACCOUNT["id"], "corr-demo-001", "synthetic", {"rules_version": RULES["version"], "outcome": "preview-only"}))
    append_audit(AuditEvent("audit-002", "2026-04-20T13:02:00Z", "demo-owner", "owner", "autopilot.previewed", ACCOUNT["id"], "corr-demo-002", "synthetic", {"evaluated_actions": preview["evaluated_count"], "would_pass": preview["would_pass_count"]}))
    # Replaying the same ID demonstrates idempotency: no duplicate is appended.
    append_audit(AUDIT_LOG[0])
    for event in read_audit_history("owner", ACCOUNT["id"]):
        print(f"  {event.at} | {event.actor} | {event.event_type} | {event.correlation_id} | {event.details}")
    print(f"Audit entries stored: {len(AUDIT_LOG)} (duplicate replay was ignored)")
    print("\nDemo complete. All figures and actions are synthetic; no money moved.")


if __name__ == "__main__":
    main()
