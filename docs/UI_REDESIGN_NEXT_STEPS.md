# Megatron UI Redesign — Next Steps

## Completed in this phase

- Added `src/megatron/web/static/theme.css` with a Cloudflare-inspired dark design system.
- Rewrote all Jinja2 templates in English with minimal icons and a unified visual style:
  - `base.html`, `login.html`, `dashboard.html`, `items.html`, `modules.html`,
    `prompts.html`, `providers.html`, `channels.html`, `schedules.html`, `runs.html`.
- Introduced new information architecture:
  - **Overview** — token/cost/run metrics and trends.
  - **Tasks** — analysis modules, schedules, and run history under one entry point.
  - **Data** — Sources / Collected / Analyzed.
  - **Prompts**, **LLM Providers**, **Webhooks**.
- Added new pages:
  - `/ui/data/sources` — source configuration entry point.
  - `/ui/data/analyzed` — structured analysis outputs and briefings.
- Mounted `/static` in `app.py` and added 302 redirects from old URLs (`/ui/items`, `/ui/modules`) to new ones.
- Fixed the dashboard double-`r.json()` bug.

## Remaining design & engineering work

### 1. Persistent source configuration
Currently source settings (e.g. `SOUNDWAVE_REPO_URL`, `list_id`, `only_dates`, `since_date`) live in `.env` or inside `AnalysisModule.source_ref`. A dedicated `source_configs` table should be added so the **Data → Sources** page can fully manage sources from the UI.

Suggested schema:
```sql
CREATE TABLE source_configs (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    source_type TEXT NOT NULL,
    config JSON DEFAULT '{}',
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Migration: add Alembic revision, keep existing modules working by reading `source_ref` as fallback.

### 2. Tasks section consolidation (deeper)
The current implementation keeps `modules.html`, `schedules.html`, and `runs.html` as separate templates but groups them under `/ui/tasks`. A more cohesive experience would be:
- `/ui/tasks` — task list.
- `/ui/tasks/{id}` — task detail + edit form + run history tab.
- `/ui/tasks/{id}/runs/{run_id}` — single run detail.
This reduces context switching and removes the need for a standalone `/ui/runs` page.

### 3. Data → Sources MCP-ready design
The page currently lists registered source plugins. To evolve toward MCP:
- Add an "Add integration" flow that lets users pick between native Source plugins and MCP servers.
- For MCP: store `mcp_server_url`, `transport` (stdio/sse), and optional `capabilities` filter.
- Implement `MCPSource` plugin that wraps an MCP client and exposes resources as `list[Item]`.
- When multiple source types exist, rename the section to **Integrations** or **MCP Config**.

### 4. Empty states, loading states, and error handling
- Replace inline "Loading..." / "Failed" text with consistent skeleton/empty-state components.
- Add toast notifications for actions like "Run started", "Saved", "Deleted" instead of `alert()`.

### 5. Forms and validation UX
- Inline validation messages instead of top-of-form error banners.
- Better provider/model selection with helper text per provider.
- Prompt editor: syntax highlight for Jinja2, collapsible template list.

### 6. Responsiveness and mobile polish
- Sidebar drawer works on mobile but some tables overflow; consider card-based mobile layouts for runs/items.
- Pagination controls should be thumb-friendly on small screens.

### 7. Theme hardening
- Pin CDN versions of DaisyUI, Tailwind, HTMX, Chart.js, marked to avoid network/version drift.
- Consider vendoring critical CSS/JS for air-gapped or slow-network deployments.

### 8. Accessibility
- Add `aria-label` to icon-only buttons.
- Ensure focus rings and color contrast meet basic a11y standards.

## Design tokens reference

```css
--bg-body: #0d0d0d;
--bg-surface: #171717;
--bg-elevated: #1f1f1f;
--border: rgba(255, 255, 255, 0.08);
--text-primary: #f5f5f5;
--text-secondary: #a3a3a3;
--text-muted: #737373;
--accent: #f48120;
--success: #22c55e;
--warning: #f59e0b;
--error: #ef4444;
--info: #3b82f6;
--radius: 6px;
```

Keep these tokens central in `theme.css`; future components should reuse them rather than hard-coding values.
