import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { sentryVitePlugin } from '@sentry/vite-plugin'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, relative, resolve } from 'node:path'
import { gzipSync } from 'node:zlib'

const frontendDir = dirname(fileURLToPath(import.meta.url))
const frontendSrc = resolve(frontendDir, 'src')
const repoRoot = resolve(frontendDir, '../..')
const devApiTarget = process.env.VITE_DEV_API_TARGET || 'http://api:8000'
const devWebSocketTarget = process.env.VITE_DEV_WEBSOCKET_TARGET || 'http://api:8001'
const bundleReportEnabled = process.env.HFL_BUNDLE_REPORT === '1'
const sentrySourceMapUpload = Boolean(
  process.env.SENTRY_AUTH_TOKEN
  && process.env.SENTRY_URL
  && process.env.SENTRY_ORG
  && process.env.SENTRY_FRONTEND_PROJECT
  && process.env.SENTRY_RELEASE,
)

function splitPathList(raw: string): string[] {
  return raw.split(',').map((p) => p.trim()).filter(Boolean)
}

function readExtensionId(root: string): string {
  const toml = resolve(root, 'extension.toml')
  if (existsSync(toml)) {
    const text = readFileSync(toml, 'utf8')
    const m = text.match(/^\s*id\s*=\s*["']([^"']+)["']/m)
    if (m?.[1]) return m[1].trim()
  }
  const base = root.split(/[/\\]/).filter(Boolean).pop() || 'extension'
  return base.replace(/^hyperfilelens-/, '') || 'extension'
}

function discoverExtensionRoots(): string[] {
  const raw = (process.env.HFL_EXTENSIONS || '').trim()
  if (!raw) return []
  const roots: string[] = []
  for (const item of splitPathList(raw)) {
    const candidate = resolve(item)
    if (!existsSync(resolve(candidate, 'src/frontend/src'))) continue
    roots.push(candidate)
  }
  return roots
}

const extensionRoots = discoverExtensionRoots()
const frontendTestScope = (process.env.HFL_FRONTEND_TEST_SCOPE || 'host').trim()
if (!['host', 'extension'].includes(frontendTestScope)) {
  throw new Error(`Unsupported HFL_FRONTEND_TEST_SCOPE: ${frontendTestScope}`)
}
const extensionFrontendSrcs = extensionRoots.map((root) => ({
  id: readExtensionId(root),
  root,
  src: resolve(root, 'src/frontend/src'),
}))
const platformExt = extensionFrontendSrcs.find((e) => e.id === 'platform')
  || extensionFrontendSrcs[0]
  || null
const platformFrontendSrc = platformExt ? platformExt.src : null
const sharedDirs = ['components', 'lib', 'composables', 'pages', 'router', 'styles', 'app', 'types'] as const

function hflExtOssBridgePlugin() {
  return {
    name: 'hfl-ext-oss-bridge',
    enforce: 'pre' as const,
    resolveId(id: string, importer?: string) {
      if (!platformFrontendSrc || !importer) return null
      if (!importer.startsWith(platformFrontendSrc)) return null
      if (!id.startsWith('.')) return null

      const abs = resolve(dirname(importer), id)
      for (const name of sharedDirs) {
        const extShared = resolve(platformFrontendSrc, name)
        if (abs === extShared || abs.startsWith(`${extShared}/`)) {
          const mapped = abs.replace(platformFrontendSrc, frontendSrc)
          for (const ext of ['', '.ts', '.tsx', '.js', '.mjs', '.vue', '/index.ts', '/index.js']) {
            const candidate = `${mapped}${ext}`
            if (existsSync(candidate)) return candidate
          }
          return mapped
        }
      }
      return null
    },
  }
}

const hostNodeModules = resolve(frontendDir, 'node_modules')
// Extensions mount outside the Vite root (e.g. /opt/hfl/extensions/<id>). Bare
// imports from those trees do not walk into Host node_modules — pin them here
// for both vite build/dev and vitest.
const extensionDepAlias = extensionFrontendSrcs.length
  ? [
      'vue',
      'vue-router',
      'vue-i18n',
      '@vue/test-utils',
      'element-plus',
      'echarts',
      'vue-echarts',
      'lucide-vue-next',
      'clsx',
      'tailwind-merge',
    ]
      .filter((name) => existsSync(resolve(hostNodeModules, name)))
      .map((name) => ({ find: name, replacement: resolve(hostNodeModules, name) }))
  : []

const extResolveAlias = [
  { find: '@host', replacement: frontendSrc },
  ...(platformFrontendSrc
    ? [
        { find: '@ext/platform/platform-ops', replacement: resolve(platformFrontendSrc, 'platform-ops') },
        { find: '@ext/platform/ops', replacement: resolve(platformFrontendSrc, 'ops') },
      ]
    : [
        { find: '@ext/platform/platform-ops', replacement: resolve(frontendSrc, 'platform-ops/ext-empty') },
        { find: '@ext/platform/ops', replacement: resolve(frontendSrc, 'ops/ext-empty') },
      ]),
  ...extensionFrontendSrcs.map((e) => ({
    find: `@ext/${e.id}`,
    replacement: e.src,
  })),
  ...extensionDepAlias,
]

for (const e of extensionFrontendSrcs) {
  console.info(`[vite] Extension frontend merged: ${e.id} ← ${e.root}`)
}

function bundleReportModuleId(moduleId: string): string {
  const normalized = moduleId.replaceAll('\\', '/')
  const nodeModulesMarker = '/node_modules/'
  const nodeModulesIndex = normalized.lastIndexOf(nodeModulesMarker)
  if (nodeModulesIndex >= 0) {
    return `node_modules/${normalized.slice(nodeModulesIndex + nodeModulesMarker.length)}`
  }
  if (normalized.startsWith(`${frontendDir}/`)) {
    return relative(frontendDir, normalized).replaceAll('\\', '/')
  }
  for (const extension of extensionFrontendSrcs) {
    if (normalized.startsWith(`${extension.src}/`)) {
      return `extensions/${extension.id}/${relative(extension.src, normalized).replaceAll('\\', '/')}`
    }
  }
  return normalized.startsWith('\0') ? normalized : relative(repoRoot, normalized).replaceAll('\\', '/')
}

function hflBundleReportPlugin(): Plugin {
  return {
    name: 'hfl-bundle-report',
    apply: 'build',
    generateBundle(_options, bundle) {
      const chunks = Object.values(bundle)
        .filter(output => output.type === 'chunk')
        .map((chunk) => ({
          fileName: chunk.fileName,
          isEntry: chunk.isEntry,
          isDynamicEntry: chunk.isDynamicEntry,
          facadeModuleId: chunk.facadeModuleId ? bundleReportModuleId(chunk.facadeModuleId) : null,
          imports: [...chunk.imports].sort(),
          dynamicImports: [...chunk.dynamicImports].sort(),
          rawBytes: Buffer.byteLength(chunk.code),
          gzipBytes: gzipSync(chunk.code).byteLength,
          modules: Object.entries(chunk.modules)
            .map(([moduleId, details]) => ({
              id: bundleReportModuleId(moduleId),
              renderedBytes: details.renderedLength,
            }))
            .sort((a, b) => b.renderedBytes - a.renderedBytes || a.id.localeCompare(b.id)),
        }))
        .sort((a, b) => b.rawBytes - a.rawBytes || a.fileName.localeCompare(b.fileName))
      const assets = Object.values(bundle)
        .filter(output => output.type === 'asset' && output.fileName.endsWith('.css'))
        .map((asset) => {
          const source = typeof asset.source === 'string' ? asset.source : Buffer.from(asset.source)
          return {
            fileName: asset.fileName,
            rawBytes: typeof source === 'string' ? Buffer.byteLength(source) : source.byteLength,
            gzipBytes: gzipSync(source).byteLength,
          }
        })
        .sort((a, b) => b.rawBytes - a.rawBytes || a.fileName.localeCompare(b.fileName))
      const reportDir = resolve(repoRoot, 'build/reports')
      mkdirSync(reportDir, { recursive: true })
      writeFileSync(
        resolve(reportDir, 'frontend-bundle.json'),
        `${JSON.stringify({ chunks, cssAssets: assets }, null, 2)}\n`,
      )
    },
  }
}

function deterministicChunkName(moduleId: string): string | undefined {
  const normalized = moduleId.replaceAll('\\', '/')
  const marker = '/node_modules/'
  const markerIndex = normalized.lastIndexOf(marker)
  if (markerIndex < 0) return undefined
  const dependencyPath = normalized.slice(markerIndex + marker.length)
  const dependency = dependencyPath.startsWith('@')
    ? dependencyPath.split('/').slice(0, 2).join('/')
    : dependencyPath.split('/')[0]

  if (dependency === 'echarts' || dependency === 'zrender') {
    return 'charts-echarts'
  }
  if (dependency.startsWith('@sentry/') || dependency.startsWith('@sentry-internal/')) {
    return 'observability-sentry'
  }
  if (
    dependency === 'codemirror'
    || dependency.startsWith('@codemirror/')
    || dependency.startsWith('@lezer/')
  ) return 'editor-codemirror'
  return undefined
}

// https://vite.dev/config/
export default defineConfig(() => ({
  envDir: repoRoot,
  define: {
    __HFL_EXTENSIONS_FRONTEND__: JSON.stringify(extensionFrontendSrcs.length > 0),
  },
  resolve: {
    alias: extResolveAlias,
  },
  test: {
    // Host and Extension tests are separate contracts. Enterprise CI runs both
    // scopes independently so Extension injection cannot change Community tests.
    include: frontendTestScope === 'extension'
      ? extensionFrontendSrcs.flatMap((e) => [
          `${e.src}/ops/**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts,jsx,tsx}`,
          `${e.src}/platform-ops/**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts,jsx,tsx}`,
        ])
      : ['src/**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts,jsx,tsx}'],
  },
  plugins: [
    ...(platformFrontendSrc ? [hflExtOssBridgePlugin()] : []),
    vue(),
    tailwindcss(),
    ...(bundleReportEnabled ? [hflBundleReportPlugin()] : []),
    ...(sentrySourceMapUpload
      ? [sentryVitePlugin({
          url: process.env.SENTRY_URL,
          authToken: process.env.SENTRY_AUTH_TOKEN,
          org: process.env.SENTRY_ORG,
          project: process.env.SENTRY_FRONTEND_PROJECT,
          telemetry: false,
          release: { name: process.env.SENTRY_RELEASE },
          sourcemaps: {
            assets: './dist/**',
            filesToDeleteAfterUpload: './dist/**/*.map',
          },
          errorHandler: (error) => {
            console.warn(`[sentry] Source Map upload failed; continuing build: ${error.message}`)
          },
        })]
      : []),
  ],
  build: {
    target: 'es2022',
    sourcemap: sentrySourceMapUpload ? 'hidden' : false,
    rollupOptions: {
      output: {
        manualChunks: deterministicChunkName,
      },
    },
  },
  optimizeDeps: {
    esbuildOptions: {
      target: 'es2022',
    },
  },
  server: {
    host: '0.0.0.0',
    allowedHosts: ['host.docker.internal'],
    port: 5173,
    strictPort: true,
    fs: {
      allow: [repoRoot, ...extensionRoots],
    },
    hmr: {
      path: '/__vite_hmr',
    },
    proxy: {
      '/api': {
        target: devApiTarget,
        changeOrigin: true,
        secure: false,
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
      '/media': {
        target: devApiTarget,
        changeOrigin: true,
        secure: false,
      },
      '/swagger': {
        target: devApiTarget,
        changeOrigin: true,
        secure: false,
      },
      '/redoc': {
        target: devApiTarget,
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: devWebSocketTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
}))
