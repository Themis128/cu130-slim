# Docker MCP toolbox first

## Always prefer the `mcp-docker` MCP server for GitHub and browser work

The `mcp-docker` MCP server (Docker MCP Toolkit) exposes **71 tools** covering
the full GitHub API surface plus Playwright-style browser automation. Use it
BEFORE falling back to `gh` CLI or ad-hoc Playwright/CDP scripts.

### GitHub surface (use instead of `gh`/`gh api`)

- **PRs**: `list_pull_requests`, `pull_request_read`, `create_pull_request`,
  `update_pull_request`, `update_pull_request_branch`, `merge_pull_request`,
  `pull_request_review_write`, `add_comment_to_pending_review`,
  `add_reply_to_pull_request_comment`, `request_copilot_review`,
  `assign_copilot_to_issue`, `pr_write_ui`, `pr_edit_ui`
- **Issues**: `list_issues`, `issue_read`, `issue_write`, `issue_write_ui`,
  `add_issue_comment`, `sub_issue_write`, `list_issue_fields`,
  `list_issue_types`
- **Repo/contents**: `get_file_contents`, `create_or_update_file`,
  `push_files`, `delete_file`, `create_branch`, `list_branches`,
  `create_repository`, `fork_repository`
- **Commits/releases**: `get_commit`, `list_commits`, `get_tag`,
  `list_tags`, `get_release_by_tag`, `get_latest_release`, `list_releases`
- **Search**: `search_code`, `search_commits`, `search_issues`,
  `search_pull_requests`, `search_repositories`, `search_users`
- **Identity/orgs**: `get_me`, `get_me_ui`, `get_teams`, `get_team_members`,
  `list_repository_collaborators`, `get_label`

Note: repo-scoped tools take `owner`/`repo` args (e.g.
`owner="Themis128"`, `repo="cu130-slim"`).

### Browser surface (Playwright-grade)

`browser_navigate`, `browser_click`, `browser_type`, `browser_fill_form`,
`browser_snapshot`, `browser_take_screenshot`, `browser_evaluate`,
`browser_run_code_unsafe`, `browser_press_key`, `browser_select_option`,
`browser_hover`, `browser_drag`, `browser_drop`, `browser_tabs`,
`browser_wait_for`, `browser_handle_dialog`, `browser_file_upload`,
`browser_console_messages`, `browser_network_request`,
`browser_network_requests`, `browser_resize`, `browser_navigate_back`,
`browser_close`.

Use this for logged-in-portal driving when the repo's own CDP lanes
(`consent-root-ops` :9222, `bridge-session-ops` :9223, sidecars) are down
or unsuitable — it is a self-contained browser, not the user's session,
so it needs its own auth (login inside the session or cookie injection).

## Fallback order

1. `mcp-docker` MCP tools.
2. Repo skill scripts (`.devin/skills/*/scripts/`, `gh`/`gh api` for GitHub).
3. Direct API/CLI. Report the fallback in the summary.

## Related skill

- `docker-mcp-ops` — sharing the Docker MCP Toolkit profile/catalog between
  Windows Docker Desktop and WSL (`docker mcp` seeing an empty profile list
  in WSL, the ~/.docker/mcp symlink fix, 9p-channel staging). Use it when the
  toolbox itself is broken or empty, not for routine tool calls.
