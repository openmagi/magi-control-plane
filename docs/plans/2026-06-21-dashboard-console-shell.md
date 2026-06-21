# Dashboard console shell redesign

> Full UX redesign of the magi-control-plane operational pages — moves
> from a single horizontal top-nav to a 2-column sidebar shell, inspired
> by the magi-agent OSS Customize tab. Marketing pages keep their own
> shell.

**Date:** 2026-06-21
**Driver:** Kevin (founder)
**Skills consulted:** `ui-ux-pro-max`, `brainstorming`
**Design system source:** `clawy/design-system/magi-control-plane/MASTER.md` + `pages/dashboard.md`

## Decisions (from brainstorming session)

| Topic | Choice | Rationale |
|-------|--------|-----------|
| Scope | Operational pages only | Marketing (`/welcome`, `/install`, `/legal/*`) stays on `NavBarShell` — different audience, different chrome. |
| IA | Domain-grouped sidebar (Authoring / Runtime / Audit / Setup) | 7 leaf pages cluster naturally; flat list would feel sparse, full mirror of magi-agent's 12 items would be over-engineered. |
| Layout | 2-column (sidebar 240px + content fluid) | Middle sub-nav column adds friction; sidebar already exposes every leaf with one click. |
| Mobile | Hamburger drawer < 1024px | Dashboard is desktop-first; drawer is the cleanest collapse. |
| Workspace card | Tenant + plan + cloud health dot | Operator context at a glance; matches magi-agent's `workspace` card semantics. |
| Footer | Hidden on console, visible on marketing | Operator pages don't need ToS/Privacy on every screen. |

## 1. Shell architecture

Next.js App Router route groups split the two shells without changing
public URLs:

```
web/app/
├── (marketing)/         ← existing chrome
│   ├── layout.tsx        topbar (NavBarShell) + content + Footer
│   ├── welcome/
│   ├── install/
│   └── legal/{terms,privacy}/
└── (console)/           ← NEW 2-column shell
    ├── layout.tsx        sidebar + main; no Footer
    ├── error.tsx         content-area error with Retry
    ├── overview/page.tsx (was app/page.tsx, the KPI summary)
    ├── policies/
    ├── verify/
    ├── hitl/
    ├── ledger/
    ├── presets/
    └── setup/
```

Root `layout.tsx` is reduced to `<html>` / `<head>` / `<body>` + skip-link
+ shared font-face. Both group layouts inherit it.

URL behaviour: `/` becomes a marketing landing redirect → `/welcome`.
`/dashboard` redirects → `/overview`. Existing operational routes
(`/policies`, `/verify`, etc.) keep their paths.

## 2. Components

New under `(console)/_components/`:

- `Sidebar.tsx` — server component, fetches workspace data + renders shell
- `SidebarClient.tsx` — client wrapper for drawer state + active-route highlight via `useSelectedLayoutSegment()`
- `WorkspaceCard.tsx` — tenant prefix + plan + cloud `/healthz` dot
- `NavGroup.tsx` — uppercase group label + children
- `NavItem.tsx` — icon + label + `aria-current="page"` + optional count badge
- `SidebarFooter.tsx` — `<LangSelect>` reused + GitHub external link
- `ConsoleHeader.tsx` — mobile-only: hamburger button + page title

Reused as-is (token-driven, no shell coupling):
`Button`, `Badge`, `Card`, `Input`, `Code`, `KPI`, `EmptyState`,
`ErrorState`, `Skeleton`, `CopyButton`, `PageHeader`, `SubmitButton`.

Icons: `@heroicons/react/24/outline` at `w-4 h-4`. No emoji — magi DS
anti-pattern.

## 3. Data flow

```ts
// (console)/_data/workspace.ts
export const getWorkspaceData = unstable_cache(
  async () => ({
    tenant: await cloud.getMyTenant().catch(() => null),
    healthOk: await checkHealth().catch(() => false),
    hitlPending: await cloud.listHitl().then(l => l.length).catch(() => 0),
  }),
  ["workspace-sidebar"],
  { revalidate: 30, tags: ["workspace"] },
)
```

Mutating server actions (HITL approve/reject, policy enable/disable)
call `revalidateTag("workspace")` so the sidebar badge updates without
a full page reload.

Active route highlighting: `SidebarClient` reads
`useSelectedLayoutSegment()`. Server `Sidebar` fetches data once per
30s window and passes a typed snapshot to the client wrapper.

Drawer state (mobile): client-side only. Open/close, ESC, backdrop tap,
auto-close on `pathname` change. `aria-expanded` + `aria-controls`
proper. Body scroll lock while open. `prefers-reduced-motion` collapses
the slide transition to 0ms.

Locale switcher: existing `<LangSelect>` server action — writes cookie,
redirects to current route, page re-renders with new locale (sidebar
included). No JS-side locale state.

Skip-link: stays at root `<body>` level, targets `#main-content` which
each layout sets on its `<main>`.

## 4. Error handling

Three failure surfaces:

1. **Sidebar data fetch fails** — `WorkspaceCard` renders an empty plan
   label + amber health dot. Sidebar nav itself is always rendered;
   navigation never breaks if the cloud is unreachable.
2. **Page render fails** — `(console)/error.tsx` boundary renders the
   sidebar + an `<ErrorState>` card in the content area with Retry.
3. **Hydration mismatch on locale** — root `<html>` has
   `suppressHydrationWarning`; sidebar internals are server-rendered
   only, no client state depends on locale.

## 5. Testing

```
(console)/_components/Sidebar.test.tsx        renders 4 groups + 7 items
(console)/_components/SidebarClient.test.tsx  drawer open/close, ESC, backdrop, route-change auto-close
(console)/_components/WorkspaceCard.test.tsx  3 states: pro_plus / self-host / fetch-fail
(console)/_data/workspace.test.ts             cache key + revalidate tags
(marketing)/layout.test.tsx                   smoke: Footer + NavBarShell present
```

`dict.test.ts` drift gate automatically catches missing `nav.group.*`
keys in KO or EN.

## 6. Sprint order

Each sprint = one commit, mergeable in isolation, no behaviour change
until the matching layout swap.

| # | Scope | Verification |
|---|-------|--------------|
| **D1** | Route-group move: 7 operational routes → `(console)/`, 3 marketing → `(marketing)/`. Each group has a placeholder layout that wraps the existing chrome — visual unchanged. | `tsc --noEmit`, every URL returns 200, vitest still 78 pass |
| **D2** | Build `Sidebar` + `WorkspaceCard` + `NavGroup` + `NavItem`. `(console)/layout.tsx` swaps `NavBarShell` for `Sidebar`. Desktop only — no mobile collapse yet. | Visual at 1440px, sidebar present, all links work |
| **D3** | `SidebarClient` drawer + active-route highlight via `useSelectedLayoutSegment()` + mobile hamburger. `ConsoleHeader` for `<1024px`. | a11y at 375 / 768 / 1024 / 1440 |
| **D4** | `getWorkspaceData` + `unstable_cache` + HITL badge + `revalidateTag("workspace")` on mutating server actions. | Server action → badge updates without reload |
| **D5** | i18n: `nav.group.{authoring,runtime,audit,setup}` keys + drift gate pass. Remove stale `nav.*` keys retired by the new IA. | `dict.test.ts` green |
| **D6** | Test suite for new components + data layer. | vitest green, no regressions in 78 pre-existing tests |
| **D7** | a11y sweep: focus trap, aria-current, keyboard nav (Tab order, ESC), `prefers-reduced-motion`. Lighthouse pass ≥ 95. | Lighthouse a11y ≥ 95 |

## Anti-patterns avoided

Per magi DS dashboard override + ui-ux-pro-max checklist:

- No emoji icons (Heroicons only)
- All clickable elements `cursor-pointer`
- Hover transitions 150–300ms, no scale transforms that shift layout
- Focus rings visible (`focus-visible:ring`)
- 4.5:1 minimum text contrast (dark mode design already complies)
- No fixed-position chrome obscuring content
- `prefers-reduced-motion` collapses motion to ≤1ms
- All numbers via `Intl.NumberFormat(locale)`, timestamps via `Intl.DateTimeFormat(locale)`

## Out of scope (deferred)

- Per-page sub-tab strip (the design spec'd an inner Tabs primitive but
  no current page needs it — add per-page as the data justifies)
- Dashboard light mode (deferred; dark-first per magi DS)
- Command palette (⌘K) — possible v2 enhancement
- Sidebar collapse-to-icons mode (desktop power-user feature, v2)
- Search across resources (v2 after we have more than 7 routes)
