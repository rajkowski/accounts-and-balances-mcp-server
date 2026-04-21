# Accounts and Balances MCP Server

<p>
<img src="https://github.com/rajkowski/accounts-and-balances/blob/main/docs/assets/accounts-and-balances-app-icon.png" alt="Accounts and Balances App Icon" width="100" height="100">
</p>

[Download Accounts and Balances on the App Store for macOS](https://apps.apple.com/us/app/accounts-balances/id6746949741) - the personal finance application required for this MCP server.

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server
that lets desktop LLMs (or any MCP client) query your personal finance data
from the **Accounts and Balances** macOS app through its AppleScript interface.

Data flows: LLM Client → MCP server (stdio) → `osascript` → Accounts and Balances.

---

## Requirements

- **Accounts and Balances** macOS app 5.0 or later must be running
- Python 3.11+
- [`mcp`](https://pypi.org/project/mcp/) package

---

## Installation

```bash
cd /path/to/accounts-and-balances-mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]"
```

For local CI-style checks, install this project in editable mode to expose
console commands:

```bash
pip install -e .
```

---

## MCP client configuration

Add the following to your Claude Desktop or LM Studio config file (or other LLM client):

**Claude macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "accounts": {
      "command": "/path/to/accounts-and-balances-mcp-server/.venv/bin/python3",
      "args": ["/path/to/accounts-and-balances-mcp-server/accounts_mcp.py"]
    }
  }
}
```

Replace `/path/to/accounts-and-balances-mcp-server` with the actual path to your cloned repository.

Restart client after saving the config.

---

## Available tools

| Tool | Description |
| --- | --- |
| `list_folders` | List all folders with account counts |
| `list_accounts(folder_name?)` | List accounts and display balances, optionally filtered to a folder |
| `get_account(account_name)` | Full account details including occurrences, snapshots, and entities |
| `list_entities(folder_name?, account_name?)` | List folder-level and account-level entities |
| `get_entity(entity_name)` | Full entity details including occurrences |
| `list_occurrences(account_name?, entity_name?, folder_name?, include_related_accounts=true)` | List occurrences globally or within a folder, account, or entity |
| `list_snapshots(account_name)` | List historical balance snapshots for an account |
| `get_upcoming_transactions(...)` | Upcoming occurrence instances over the next N days |
| `get_upcoming_occurrences(...)` | Alias for `get_upcoming_transactions` using current terminology |
| `project_balance(account_name, days=30)` | Day-by-day projected balance using the same balance semantics as the app |

## Notes on semantics

- Account and snapshot balances use AppleScript display values. Liability balances are returned as positive amounts owed.
- Account-scoped occurrence queries and balance projections include transfer occurrences where the selected account is the related account, matching the app's current AppleScript balance behavior.
- The server remains read-only even though the app's AppleScript surface also supports writes.

---

## Example prompts for LLMs

- "What are my current account balances?"
- "What bills are due in the next two weeks?"
- "Show me the projected balance for my Checking account over the next 3 months."
- "Which accounts are below their minimum balance threshold?"
- "Show me the snapshots for my Credit Card account."
- "List all occurrences tied to my Utilities entity."

---

## Smoke test command

Run a simple end-to-end MCP smoke test (server init + tool calls):

```bash
accounts-mcp-smoke
```

Equivalent command without installing console scripts:

```bash
python3 smoke_test_mcp.py
```

---

## Troubleshooting

**"AppleScript error: Connection is invalid"**
Make sure the Accounts and Balances app is running before using any tool.

**"Application isn't running"**
Open the Accounts and Balances app on your Mac, then retry.

**Permissions prompt**
On first use, macOS may ask whether Claude (or Terminal) can control the Accounts and Balances app via AppleScript. Click **OK** to allow it.

**No data returned**
Make sure **Enable AppleScript** is turned on in Accounts and Balances under Settings → General → Automation.
