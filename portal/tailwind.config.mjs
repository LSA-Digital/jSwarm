// COM-119 — Tailwind v3 config (Context7-verified: /websites/v3_tailwindcss + /withastro/docs).
// Theme tokens ported from COM-82 `scripts/security/project_dashboard.py` for
// visual continuity across the migrated dashboards.
//
// COM-127 T3.5 — professional styling pass: added semantic staleness/severity
// scale (jw-high / jw-med / jw-low + soft bg tints) consumed by StalenessBadge
// and the Risk/Maintenance matrix rank-coloring map. Deterministic / static.
/** @type {import('tailwindcss').Config} */
export default {
  // T3.5 safelist: dynamic class lookups (rank-coloring + StalenessBadge tone
  // maps) are constructed by string concatenation, so Tailwind's purge scanner
  // can't see them in source. Safelist the closed set explicitly.
  safelist: [
    "bg-jw-high-soft",
    "text-jw-high",
    "border-jw-high",
    "bg-jw-med-soft",
    "text-jw-med",
    "border-jw-med",
    "bg-jw-low-soft",
    "text-jw-low",
    "border-jw-low",
    "bg-jw-crit-soft",
    "text-jw-crit",
    "border-jw-crit",
    "bg-jw-info-soft",
    "text-jw-info",
    "border-jw-info",
    "bg-jw-status-pass-soft",
    "text-jw-status-pass",
    "border-jw-status-pass",
    "bg-jw-status-fail-soft",
    "text-jw-status-fail",
    "border-jw-status-fail",
    "bg-jw-status-caution-soft",
    "text-jw-status-caution",
    "border-jw-status-caution",
    "bg-jw-status-na-soft",
    "text-jw-status-na",
    "border-jw-status-na",
  ],
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        // Portal-wide semantic colors are CSS-variable backed so the same
        // utility classes remain truthful in light, dark, and explicit modes.
        'jw-blue': 'rgb(var(--jw-blue) / <alpha-value>)',
        'jw-on-accent': 'rgb(var(--jw-on-accent) / <alpha-value>)',
        'jw-red': 'rgb(var(--jw-red) / <alpha-value>)',
        'jw-line': 'rgb(var(--jw-line) / <alpha-value>)',
        'jw-muted': 'rgb(var(--jw-muted) / <alpha-value>)',
        'jw-bg': 'rgb(var(--jw-bg) / <alpha-value>)',
        'jw-fg': 'rgb(var(--jw-fg) / <alpha-value>)',
        'jw-warn': 'rgb(var(--jw-warn) / <alpha-value>)',
        'jw-ok': 'rgb(var(--jw-ok) / <alpha-value>)',
        'jw-surface': 'rgb(var(--jw-surface) / <alpha-value>)',
        'jw-surface-muted': 'rgb(var(--jw-surface-muted) / <alpha-value>)',
        'jw-header-bg': 'rgb(var(--jw-header-bg) / <alpha-value>)',
        'jw-queue': 'rgb(var(--jw-queue) / <alpha-value>)',
        'jw-queue-selected': 'rgb(var(--jw-queue-selected) / <alpha-value>)',
        'jw-high': 'rgb(var(--jw-high) / <alpha-value>)',
        'jw-high-soft': 'rgb(var(--jw-high-soft) / <alpha-value>)',
        'jw-med': 'rgb(var(--jw-med) / <alpha-value>)',
        'jw-med-soft': 'rgb(var(--jw-med-soft) / <alpha-value>)',
        'jw-low': 'rgb(var(--jw-low) / <alpha-value>)',
        'jw-low-soft': 'rgb(var(--jw-low-soft) / <alpha-value>)',
        'jw-crit': 'rgb(var(--jw-crit) / <alpha-value>)',
        'jw-crit-soft': 'rgb(var(--jw-crit-soft) / <alpha-value>)',
        'jw-info': 'rgb(var(--jw-info) / <alpha-value>)',
        'jw-info-soft': 'rgb(var(--jw-info-soft) / <alpha-value>)',
        'jw-status-pass': 'rgb(var(--jw-status-pass) / <alpha-value>)',
        'jw-status-pass-soft': 'rgb(var(--jw-status-pass-soft) / <alpha-value>)',
        'jw-status-fail': 'rgb(var(--jw-status-fail) / <alpha-value>)',
        'jw-status-fail-soft': 'rgb(var(--jw-status-fail-soft) / <alpha-value>)',
        'jw-status-caution': 'rgb(var(--jw-status-caution) / <alpha-value>)',
        'jw-status-caution-soft': 'rgb(var(--jw-status-caution-soft) / <alpha-value>)',
        'jw-status-na': 'rgb(var(--jw-status-na) / <alpha-value>)',
        'jw-status-na-soft': 'rgb(var(--jw-status-na-soft) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        mono: ['SF Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      spacing: {
        // 18 = 4.5rem — used for the header band height
        '18': '4.5rem',
      },
    },
  },
  plugins: [],
};
