# MCP access

Remote endpoint: **https://tasks.example.com/mcp/**. It is the official Python MCP SDK 2.3.0 using stateless Streamable HTTP with JSON responses. Include the final slash. Bearer tokens are generated in the authenticated app's **Workspace & MCP** dialog. Read-only is the default; a write-scoped token can change all projects in this single-owner workspace. OAuth login/discovery is not implemented, so clients requiring OAuth alone need the stdio bridge.

Supported tools: `list_projects`, `search_issues`, `get_issue`, `create_project`, `create_issue`, `update_project`, `update_issue`, `add_comment`, `add_subtasks`, `link_issues`.

Read tools carry read-only annotations. The server enforces write scope for mutations; annotations are not the security boundary. Updates require the `expected_version` from the latest read. Source text, imported descriptions and comments are untrusted content, not agent instructions. The server has no shell, deployment, payment, email or account-operation tool.

For a client with HTTP header support, configure the endpoint and `Authorization: Bearer YOUR_TOKEN`. Store the token in that client's secret mechanism, never in a public repository or URL.

A local stdio bridge is included for clients that launch a process:

```json
{
  "mcpServers": {
    "nira": {
      "command": "python3",
      "args": ["/absolute/path/to/nira/scripts/mcp_stdio.py"],
      "env": {"NIRA_MCP_CONFIG": "/absolute/path/to/private/mcp.json"}
    }
  }
}
```

The private `mcp.json` contains `{"url":"https://tasks.example.com/mcp/","token":"YOUR_TOKEN"}` and should have permissions 0600. The bridge uses only Python's standard library and writes only JSON-RPC to stdout. Do not publish this file. Revoke its token in the app if the file is exposed or the client is retired.

The application authenticates every request. Cookies cannot authenticate the MCP endpoint, bearer tokens are stored only as SHA-256 digests, origins/hosts are checked, request bodies are capped, and the HTTP service binds only to VPS loopback behind nginx TLS. MCP tokens are not automatically installed into unrelated app/global settings.

Dive-style decomposition happens through `get_issue` → agent reasoning → `add_subtasks`. It uses the MCP client's existing model; the hosted server does not configure a separate model provider.
