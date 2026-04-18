#!/usr/bin/env python3
# Copyright 2026 Matt Rajkowski
# SPDX-License-Identifier: Apache-2.0
"""MCP server for the Accounts macOS app via AppleScript.

The server is intentionally read-only, but it mirrors the current AppleScript
surface closely enough to expose folders, accounts, entities, occurrences,
snapshots, upcoming activity, and projected balances.
"""

import calendar
import subprocess
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from mcp.server.fastmcp import FastMCP  # pyright: ignore[reportMissingImports]

MISSING_VALUE = "missing value"
FIELD_SEPARATOR = "\x1f"
ROW_SEPARATOR = "\x1e"
DISTANT_FUTURE_YEAR = 3000
NAME_FIELD = "name of itemRef"
ID_FIELD = "id of itemRef"
NOTES_FIELD = "notes of itemRef"


mcp = FastMCP(
    "Accounts",
    instructions=(
        "Read-only access to the Accounts and Balances macOS app through "
        "AppleScript. Balances match the app's AppleScript display values, "
        "including positive liability balances, and account projections include "
        "transfer occurrences that affect the selected account."
    ),
)


def _run_raw_applescript(script: str) -> str:
    """Run an AppleScript without an implicit tell block and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "AppleScript error"
        raise RuntimeError(f"AppleScript error (exit {result.returncode}): {stderr}")
    return result.stdout.strip()


def _escape(value: str) -> str:
    """Escape double quotes for safe interpolation into AppleScript strings."""
    return value.replace('"', '\\"')


def _parse_decimal(value: str) -> float:
    """Convert a serialized AppleScript numeric value to float."""
    try:
        return float(value.strip().replace(",", ""))
    except (AttributeError, ValueError):
        return 0.0


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1"}


def _parse_as_date(value: str) -> Optional[date]:
    """Parse normalized AppleScript ISO dates into Python dates."""
    raw = value.strip()
    if not raw or raw == MISSING_VALUE:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _optional_text(value: str) -> str:
    raw = value.strip()
    return "" if not raw or raw == MISSING_VALUE else raw


def _optional_date(value: str) -> Optional[date]:
    parsed = _parse_as_date(value)
    if parsed and parsed.year >= DISTANT_FUTURE_YEAR:
        return None
    return parsed


def _serialize_records(object_expression: str, field_expressions: Sequence[str]) -> str:
    """Return AppleScript objects serialized with stable field and row separators."""
    # Keep list literal fields on one line; osascript -e can reject multiline
    # literals here with "Expected expression ... but found end of line".
    joined_fields = ", ".join(field_expressions)
    script = f'''use framework "Foundation"
use scripting additions

property fieldSep : ASCII character 31
property rowSep : ASCII character 30
property formatter : missing value

on ensureFormatter()
    if formatter is missing value then
        set formatter to current application's NSDateFormatter's alloc()'s init()
        formatter's setLocale:(current application's NSLocale's localeWithLocaleIdentifier:"en_US_POSIX")
        formatter's setDateFormat:"yyyy-MM-dd"
    end if
end ensureFormatter

on normalizedText(value)
    if value is missing value then return ""
    if class of value is date then
        my ensureFormatter()
        return (formatter's stringFromDate:value) as text
    end if
    if class of value is boolean then
        if value then return "true"
        return "false"
    end if
    return value as text
end normalizedText

on optionalObjectName(value)
    if value is missing value then return ""
    try
        return name of value
    on error
        return my normalizedText(value)
    end try
end optionalObjectName

on joinValues(values)
    set normalized to {{}}
    repeat with valueRef in values
        set end of normalized to my normalizedText(contents of valueRef)
    end repeat
    set previousDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to fieldSep
    set joinedValue to normalized as text
    set AppleScript's text item delimiters to previousDelimiters
    return joinedValue
end joinValues

tell application "Accounts and Balances"
    set outputRows to {{}}
    repeat with itemRef in {object_expression}
        set end of outputRows to my joinValues({{{joined_fields}}})
    end repeat
end tell

set previousDelimiters to AppleScript's text item delimiters
set AppleScript's text item delimiters to rowSep
set joinedRows to outputRows as text
set AppleScript's text item delimiters to previousDelimiters
return joinedRows'''
    return _run_raw_applescript(script)


def _parse_records(raw: str, field_names: Sequence[str]) -> list[dict[str, str]]:
    """Parse serialized AppleScript records into keyed dictionaries."""
    if not raw:
        return []

    records: list[dict[str, str]] = []
    for row in raw.split(ROW_SEPARATOR):
        if not row:
            continue
        values = row.split(FIELD_SEPARATOR)
        if len(values) < len(field_names):
            values.extend([""] * (len(field_names) - len(values)))
        records.append(dict(zip(field_names, values[: len(field_names)])))
    return records


def _dedupe_by_id(items: Sequence[dict]) -> list[dict]:
    """Keep the first occurrence of each object by id."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        item_id = str(item.get("id", "")).strip()
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        deduped.append(item)
    return deduped


def _sort_folders(folders: Sequence[dict]) -> list[dict]:
    return sorted(folders, key=lambda folder: folder["name"].lower())


def _sort_accounts(accounts: Sequence[dict]) -> list[dict]:
    return sorted(accounts, key=lambda account: (account["folder"].lower(), account["name"].lower()))


def _sort_entities(entities: Sequence[dict]) -> list[dict]:
    return sorted(
        entities,
        key=lambda entity: (
            entity["folder"].lower(),
            entity["account"].lower(),
            entity["name"].lower(),
        ),
    )


def _sort_occurrences(occurrences: Sequence[dict]) -> list[dict]:
    sentinel = date.max
    return sorted(
        occurrences,
        key=lambda occurrence: (
            occurrence.get("starts_on") or sentinel,
            occurrence["name"].lower(),
            occurrence["account"].lower(),
        ),
    )


def _sort_snapshots(snapshots: Sequence[dict]) -> list[dict]:
    sentinel = date.min
    return sorted(
        snapshots,
        key=lambda snapshot: snapshot.get("date") or sentinel,
    )


def _folder_object_expression(folder_name: str) -> str:
    return f'folder "{_escape(folder_name)}"'


def _account_object_expression(account_name: str) -> str:
    return f'(first account whose name is "{_escape(account_name)}")'


def _entity_object_expression(entity_name: str) -> str:
    return f'(first entity whose name is "{_escape(entity_name)}")'


def _fetch_folders() -> list[dict]:
    rows = _parse_records(
        _serialize_records(
            "every folder",
            [
                NAME_FIELD,
                ID_FIELD,
                "account count of itemRef",
            ],
        ),
        ("name", "id", "account_count"),
    )
    folders = [
        {
            "name": row["name"],
            "id": row["id"],
            "account_count": int(row["account_count"] or 0),
        }
        for row in rows
    ]
    return _sort_folders(folders)


def _fetch_accounts_in_folder(folder_name: str) -> list[dict]:
    rows = _parse_records(
        _serialize_records(
            f'every account of {_folder_object_expression(folder_name)}',
            [
                NAME_FIELD,
                ID_FIELD,
                "balance of itemRef",
                "is asset of itemRef",
                "interest rate of itemRef",
                "tolerance of itemRef",
                NOTES_FIELD,
                "icon of itemRef",
            ],
        ),
        (
            "name",
            "id",
            "balance",
            "is_asset",
            "interest_rate",
            "tolerance",
            "notes",
            "icon",
        ),
    )
    return [
        {
            "name": row["name"],
            "id": row["id"],
            "folder": folder_name,
            "balance": _parse_decimal(row["balance"]),
            "is_asset": _as_bool(row["is_asset"]),
            "interest_rate": _parse_decimal(row["interest_rate"]),
            "tolerance": _parse_decimal(row["tolerance"]),
            "notes": _optional_text(row["notes"]),
            "icon": _optional_text(row["icon"]),
        }
        for row in rows
    ]


def _fetch_accounts(folder_name: Optional[str] = None) -> list[dict]:
    if folder_name:
        return _sort_accounts(_fetch_accounts_in_folder(folder_name))

    accounts: list[dict] = []
    for folder in _fetch_folders():
        accounts.extend(_fetch_accounts_in_folder(folder["name"]))
    return _sort_accounts(accounts)


def _find_account(account_name: str) -> Optional[dict]:
    lowered = account_name.lower()
    return next((account for account in _fetch_accounts() if account["name"].lower() == lowered), None)


def _fetch_entities_in_folder(folder_name: str) -> list[dict]:
    rows = _parse_records(
        _serialize_records(
            f'every entity of {_folder_object_expression(folder_name)}',
            [
                NAME_FIELD,
                ID_FIELD,
                NOTES_FIELD,
            ],
        ),
        ("name", "id", "notes"),
    )
    return [
        {
            "name": row["name"],
            "id": row["id"],
            "notes": _optional_text(row["notes"]),
            "folder": folder_name,
            "account": "",
        }
        for row in rows
    ]


def _fetch_entities_in_account(account_name: str, folder_name: str = "") -> list[dict]:
    rows = _parse_records(
        _serialize_records(
            f'every entity of {_account_object_expression(account_name)}',
            [
                NAME_FIELD,
                ID_FIELD,
                NOTES_FIELD,
            ],
        ),
        ("name", "id", "notes"),
    )
    return [
        {
            "name": row["name"],
            "id": row["id"],
            "notes": _optional_text(row["notes"]),
            "folder": folder_name,
            "account": account_name,
        }
        for row in rows
    ]


def _fetch_entities(
    folder_name: Optional[str] = None,
    account_name: Optional[str] = None,
) -> list[dict]:
    if account_name:
        account = _find_account(account_name)
        if account is None:
            return []
        return _sort_entities(_fetch_entities_in_account(account_name, folder_name=account["folder"]))

    if folder_name:
        entities = _fetch_entities_in_folder(folder_name)
        for account in _fetch_accounts_in_folder(folder_name):
            entities.extend(_fetch_entities_in_account(account["name"], folder_name=folder_name))
        return _sort_entities(_dedupe_by_id(entities))

    entities: list[dict] = []
    for folder in _fetch_folders():
        entities.extend(_fetch_entities(folder_name=folder["name"]))
    return _sort_entities(_dedupe_by_id(entities))


def _occurrence_from_row(row: dict[str, str]) -> dict:
    starts_on = _parse_as_date(row["starts_on"])
    return {
        "name": row["name"],
        "id": row["id"],
        "account": _optional_text(row["account"]),
        "amount": _parse_decimal(row["amount"]),
        "transaction_type": _optional_text(row["transaction_type"]),
        "frequency": _optional_text(row["frequency"]),
        "interval": max(1, int(row["interval"] or 1)),
        "starts_on": starts_on,
        "due_on": _parse_as_date(row["due_on"]),
        "ends_on": _optional_date(row["ends_on"]),
        "memo": _optional_text(row["memo"]),
        "related_account": _optional_text(row["related_account"]),
    }


def _fetch_occurrences_for_expression(object_expression: str) -> list[dict]:
    rows = _parse_records(
        _serialize_records(
            object_expression,
            [
                NAME_FIELD,
                ID_FIELD,
                "my optionalObjectName(account of itemRef)",
                "amount of itemRef",
                "transaction type of itemRef",
                "frequency of itemRef",
                "interval of itemRef",
                "starts on of itemRef",
                "due on of itemRef",
                "ends on of itemRef",
                "memo of itemRef",
                "my optionalObjectName(related account of itemRef)",
            ],
        ),
        (
            "name",
            "id",
            "account",
            "amount",
            "transaction_type",
            "frequency",
            "interval",
            "starts_on",
            "due_on",
            "ends_on",
            "memo",
            "related_account",
        ),
    )
    return [_occurrence_from_row(row) for row in rows]


def _fetch_folder_occurrences(folder_name: str) -> list[dict]:
    return _sort_occurrences(
        _fetch_occurrences_for_expression(
            f'every occurrence of every account of {_folder_object_expression(folder_name)}'
        )
    )


def _fetch_entity_occurrences(entity_name: str) -> list[dict]:
    return _sort_occurrences(
        _fetch_occurrences_for_expression(
            f'every occurrence of {_entity_object_expression(entity_name)}'
        )
    )


def _fetch_account_occurrences(
    account_name: str,
    include_related_accounts: bool = True,
) -> list[dict]:
    account = _find_account(account_name)
    if account is None:
        return []

    occurrences = _fetch_folder_occurrences(account["folder"])
    if include_related_accounts:
        filtered = [
            occurrence
            for occurrence in occurrences
            if occurrence["account"].lower() == account_name.lower()
            or occurrence["related_account"].lower() == account_name.lower()
        ]
    else:
        filtered = [
            occurrence
            for occurrence in occurrences
            if occurrence["account"].lower() == account_name.lower()
        ]
    return _sort_occurrences(_dedupe_by_id(filtered))


def _fetch_occurrences(
    account_name: Optional[str] = None,
    entity_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    include_related_accounts: bool = True,
) -> list[dict]:
    if entity_name:
        return _fetch_entity_occurrences(entity_name)
    if account_name:
        return _fetch_account_occurrences(
            account_name,
            include_related_accounts=include_related_accounts,
        )
    if folder_name:
        return _fetch_folder_occurrences(folder_name)

    occurrences: list[dict] = []
    for folder in _fetch_folders():
        occurrences.extend(_fetch_folder_occurrences(folder["name"]))
    return _sort_occurrences(_dedupe_by_id(occurrences))


def _fetch_snapshots(account_name: str) -> list[dict]:
    rows = _parse_records(
        _serialize_records(
            f'every snapshot of {_account_object_expression(account_name)}',
            [
                ID_FIELD,
                "balance of itemRef",
                "adjustment of itemRef",
                "interest rate of itemRef",
                "date of itemRef",
                "memo of itemRef",
            ],
        ),
        ("id", "balance", "adjustment", "interest_rate", "date", "memo"),
    )
    snapshots = [
        {
            "id": row["id"],
            "account": account_name,
            "balance": _parse_decimal(row["balance"]),
            "adjustment": _parse_decimal(row["adjustment"]),
            "interest_rate": _parse_decimal(row["interest_rate"]),
            "date": _parse_as_date(row["date"]),
            "memo": _optional_text(row["memo"]),
        }
        for row in rows
    ]
    return _sort_snapshots(snapshots)


def _matches_monthly_interval(occurrence: dict, check_date: date, starts_on: date) -> bool:
    if check_date.day != starts_on.day:
        last_day = calendar.monthrange(check_date.year, check_date.month)[1]
        if not (check_date.day == last_day and starts_on.day >= last_day):
            return False
    diff_months = (check_date.year - starts_on.year) * 12 + (check_date.month - starts_on.month)
    return diff_months % occurrence["interval"] == 0


def _matches_weekly_interval(occurrence: dict, check_date: date, starts_on: date) -> bool:
    diff_days = (check_date - starts_on).days
    return diff_days % (occurrence["interval"] * 7) == 0


def _matches_daily_interval(occurrence: dict, check_date: date, starts_on: date) -> bool:
    diff_days = (check_date - starts_on).days
    return diff_days % occurrence["interval"] == 0


def _matches_yearly_interval(check_date: date, starts_on: date) -> bool:
    return check_date.month == starts_on.month and check_date.day == starts_on.day


def _is_occurrence_for_date(occurrence: dict, check_date: date) -> bool:
    starts_on = occurrence.get("starts_on")
    if starts_on is None or check_date < starts_on:
        return False

    ends_on = occurrence.get("ends_on")
    if ends_on and check_date > ends_on:
        return False

    frequency = occurrence.get("frequency", "").lower()
    if frequency == "once":
        return check_date == starts_on
    if frequency == "monthly":
        return _matches_monthly_interval(occurrence, check_date, starts_on)
    if frequency == "weekly":
        return _matches_weekly_interval(occurrence, check_date, starts_on)
    if frequency == "daily":
        return _matches_daily_interval(occurrence, check_date, starts_on)
    if frequency == "yearly":
        return _matches_yearly_interval(check_date, starts_on)
    return False


def _balance_effect_for_account(occurrence: dict, account_name: str) -> float:
    account_name_lower = account_name.lower()
    amount = occurrence.get("amount", 0.0)
    transaction_type = occurrence.get("transaction_type", "").lower()
    occurrence_account = occurrence.get("account", "").lower()
    related_account = occurrence.get("related_account", "").lower()

    if transaction_type == "addition":
        return amount if occurrence_account == account_name_lower else 0.0
    if transaction_type == "transfer":
        if related_account == account_name_lower:
            return amount
        if occurrence_account == account_name_lower:
            return -amount
        return 0.0
    return -amount if occurrence_account == account_name_lower else 0.0


def _project_balance(balance: float, account_name: str, occurrences: Sequence[dict], days: int) -> list[dict]:
    """Project a running balance from today forward using occurrence rules."""
    today = date.today()
    running_balance = balance
    projection: list[dict] = []

    for offset in range(days + 1):
        check_date = today + timedelta(days=offset)
        if offset > 0:
            for occurrence in occurrences:
                if _is_occurrence_for_date(occurrence, check_date):
                    running_balance += _balance_effect_for_account(occurrence, account_name)
        projection.append(
            {
                "date": check_date.isoformat(),
                "balance": round(running_balance, 2),
            }
        )

    return projection


def _upcoming_dates_for_occurrence(occurrence: dict, days: int) -> list[date]:
    today = date.today()
    return [
        today + timedelta(days=offset)
        for offset in range(days + 1)
        if _is_occurrence_for_date(occurrence, today + timedelta(days=offset))
    ]


@mcp.tool()
def list_folders() -> list[dict]:
    """List all folders with their AppleScript ids and account counts."""
    return _fetch_folders()


@mcp.tool()
def list_accounts(folder_name: Optional[str] = None) -> list[dict]:
    """List accounts, optionally filtered to a folder.

    Returned balances are AppleScript display balances, so liabilities are
    reported as positive amounts owed.
    """
    return _fetch_accounts(folder_name=folder_name)


@mcp.tool()
def get_account(account_name: str) -> dict:
    """Get one account with its occurrences, snapshots, and account-scoped entities."""
    account = _find_account(account_name)
    if account is None:
        return {"error": f"Account '{account_name}' not found."}

    occurrences = _fetch_account_occurrences(account_name, include_related_accounts=True)
    entities = _fetch_entities(account_name=account_name)
    snapshots = _fetch_snapshots(account_name)
    return {
        **account,
        "entities": entities,
        "occurrences": occurrences,
        "entity_events": occurrences,
        "snapshots": snapshots,
    }


@mcp.tool()
def list_entities(
    folder_name: Optional[str] = None,
    account_name: Optional[str] = None,
) -> list[dict]:
    """List entities globally or within a specific folder or account."""
    return _fetch_entities(folder_name=folder_name, account_name=account_name)


@mcp.tool()
def get_entity(entity_name: str) -> dict:
    """Get one entity with its occurrences."""
    entity = next(
        (item for item in _fetch_entities() if item["name"].lower() == entity_name.lower()),
        None,
    )
    if entity is None:
        return {"error": f"Entity '{entity_name}' not found."}

    return {
        **entity,
        "occurrences": _fetch_entity_occurrences(entity_name),
    }


@mcp.tool()
def list_occurrences(
    account_name: Optional[str] = None,
    entity_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    include_related_accounts: bool = True,
) -> list[dict]:
    """List occurrences globally or scoped to a folder, account, or entity.

    For account queries, related-account transfer occurrences are included by
    default so the returned data matches the account balance projection logic.
    """
    occurrences = _fetch_occurrences(
        account_name=account_name,
        entity_name=entity_name,
        folder_name=folder_name,
        include_related_accounts=include_related_accounts,
    )
    if account_name:
        return [
            {
                **occurrence,
                "balance_effect": _balance_effect_for_account(occurrence, account_name),
            }
            for occurrence in occurrences
        ]
    return occurrences


@mcp.tool()
def list_snapshots(account_name: str) -> list[dict]:
    """List snapshots for an account using AppleScript display balances."""
    account = _find_account(account_name)
    if account is None:
        return [{"error": f"Account '{account_name}' not found."}]
    return _fetch_snapshots(account_name)


@mcp.tool()
def get_upcoming_transactions(
    account_name: Optional[str] = None,
    entity_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    days: int = 30,
    include_related_accounts: bool = True,
) -> list[dict]:
    """Return occurrence instances due within the next N days.

    The existing tool name is preserved for compatibility, even though the
    AppleScript surface now uses the term occurrence.
    """
    if days < 1 or days > 365:
        return [{"error": "days must be between 1 and 365"}]

    occurrences = _fetch_occurrences(
        account_name=account_name,
        entity_name=entity_name,
        folder_name=folder_name,
        include_related_accounts=include_related_accounts,
    )
    if account_name and not _find_account(account_name):
        return [{"error": f"Account '{account_name}' not found."}]

    upcoming: list[dict] = []
    for occurrence in occurrences:
        for scheduled_date in _upcoming_dates_for_occurrence(occurrence, days):
            record = {
                "account": occurrence["account"],
                "name": occurrence["name"],
                "amount": occurrence["amount"],
                "transaction_type": occurrence["transaction_type"],
                "frequency": occurrence["frequency"],
                "interval": occurrence["interval"],
                "date": scheduled_date.isoformat(),
                "due_on": occurrence["due_on"],
                "ends_on": occurrence["ends_on"],
                "memo": occurrence["memo"],
                "related_account": occurrence["related_account"],
            }
            if account_name:
                record["balance_effect"] = _balance_effect_for_account(occurrence, account_name)
            upcoming.append(record)

    return sorted(upcoming, key=lambda item: (item["date"], item["account"].lower(), item["name"].lower()))


@mcp.tool()
def get_upcoming_occurrences(
    account_name: Optional[str] = None,
    entity_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    days: int = 30,
    include_related_accounts: bool = True,
) -> list[dict]:
    """Alias for get_upcoming_transactions using current occurrence terminology."""
    return get_upcoming_transactions(
        account_name=account_name,
        entity_name=entity_name,
        folder_name=folder_name,
        days=days,
        include_related_accounts=include_related_accounts,
    )


@mcp.tool()
def project_balance(account_name: str, days: int = 90) -> list[dict]:
    """Project an account's running balance using current AppleScript semantics."""
    if days < 1 or days > 365:
        return [{"error": "days must be between 1 and 365"}]

    account = _find_account(account_name)
    if account is None:
        return [{"error": f"Account '{account_name}' not found."}]

    occurrences = _fetch_account_occurrences(account_name, include_related_accounts=True)
    return _project_balance(
        balance=account["balance"],
        account_name=account_name,
        occurrences=occurrences,
        days=days,
    )


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
