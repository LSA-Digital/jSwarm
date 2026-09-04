// @ts-check
// COM-389 — Astro build config for the decision-review render tier (sibling of
// dashboard-ui; same static-first conventions). Static build only: one review
// page per input contract, driven by DECISION_REVIEW_DATA_FILE set by the
// Python wrapper (jswarm/portal/render_ui.py).
import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// Build-time determinism: TZ=UTC, no telemetry, no random ports, no Date.now
// in rendered output, no generator meta. The Python wrapper sets these envs
// before invoking us.
export default defineConfig({
  output: 'static',
  build: {
    inlineStylesheets: 'never',
    assets: '_assets',
    format: 'directory',
  },
  vite: {
    build: {
      sourcemap: false, // deterministic publish builds.
      target: 'es2022',
      reportCompressedSize: false,
    },
  },
  integrations: [
    tailwind({
      configFile: './tailwind.config.mjs',
      applyBaseStyles: true,
    }),
  ],
  server: { port: 0 },
});
