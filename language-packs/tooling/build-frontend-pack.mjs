#!/usr/bin/env node
import { createRequire } from 'node:module'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const packDir = path.resolve(process.argv[2] ?? '')
const outputDir = path.resolve(process.argv[3] ?? '')
const version = process.argv[4] ?? ''
if (!packDir || !outputDir || !/^(?:\d+\.\d+\.\d+(?:[.-][A-Za-z0-9][A-Za-z0-9.-]*)?|main-[0-9a-f]{7})$/.test(version)) {
  throw new Error('usage: build-frontend-pack.mjs PACK_DIR OUTPUT_DIR VERSION')
}

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const require = createRequire(path.join(repoRoot, 'src/frontend/package.json'))
const esbuild = require('esbuild')
const definition = JSON.parse(fs.readFileSync(path.join(packDir, 'definition.json'), 'utf8'))
const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hfl-language-pack-'))

async function bundleDefault(entry) {
  const output = path.join(tempDir, `${crypto.randomUUID()}.mjs`)
  await esbuild.build({
    entryPoints: [entry],
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: ['node20'],
    outfile: output,
    logLevel: 'silent',
  })
  return (await import(`${pathToFileURL(output).href}?v=${crypto.randomUUID()}`)).default
}

function isPlainData(value) {
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return true
  if (Array.isArray(value)) return value.every(isPlainData)
  if (typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) return false
  return Object.values(value).every(isPlainData)
}

function flatten(value, prefix = '', result = new Map()) {
  for (const [key, child] of Object.entries(value ?? {})) {
    const fullKey = prefix ? `${prefix}.${key}` : key
    if (child !== null && typeof child === 'object' && !Array.isArray(child)) {
      flatten(child, fullKey, result)
    } else {
      result.set(fullKey, child)
    }
  }
  return result
}

function placeholders(value) {
  if (typeof value !== 'string') return []
  return [...value.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map((match) => match[1]).sort()
}

function sourceDigest(value) {
  return crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex')
}

function failContract(english, translation) {
  const englishFlat = flatten(english)
  const translatedFlat = flatten(translation)
  const missing = [...englishFlat.keys()].filter((key) => !translatedFlat.has(key))
  const extra = [...translatedFlat.keys()].filter((key) => !englishFlat.has(key))
  const placeholderMismatch = [...englishFlat.keys()].filter(
    (key) => translatedFlat.has(key) &&
      JSON.stringify(placeholders(englishFlat.get(key))) !==
        JSON.stringify(placeholders(translatedFlat.get(key))),
  )
  if (!missing.length && !extra.length && !placeholderMismatch.length) return
  const lines = []
  for (const [label, values] of [
    ['missing', missing],
    ['extra', extra],
    ['placeholder mismatch', placeholderMismatch],
  ]) {
    if (!values.length) continue
    lines.push(`${label} (${values.length}):`)
    lines.push(...values.slice(0, 50).map((key) => `  - ${key}`))
    if (values.length > 50) lines.push(`  ... and ${values.length - 50} more`)
  }
  throw new Error(`translation contract failed\n${lines.join('\n')}`)
}

try {
  const englishWrapper = path.join(tempDir, 'english.ts')
  fs.writeFileSync(
    englishWrapper,
    `import { en } from ${JSON.stringify(path.join(repoRoot, 'src/frontend/src/locales/en.ts'))}\nexport default en\n`,
  )
  const messages = await bundleDefault(path.join(packDir, 'frontend/src/entry.ts'))
  const english = await bundleDefault(englishWrapper)
  const sourceLock = JSON.parse(
    fs.readFileSync(path.join(packDir, 'frontend/source-lock.json'), 'utf8'),
  )
  const allowedEnglish = new Set(
    JSON.parse(fs.readFileSync(path.join(packDir, 'frontend/allowed-english.json'), 'utf8')),
  )
  const componentLocale = await import(
    `${pathToFileURL(require.resolve(`element-plus/dist/locale/${definition.component_locale}.mjs`)).href}`
  ).then((module) => module.default)

  if (!isPlainData(messages) || !isPlainData(componentLocale)) {
    throw new Error('frontend language pack must contain JSON-compatible data only')
  }
  failContract(english, messages)

  const englishFlat = flatten(english)
  const translatedFlat = flatten(messages)
  const stale = [...englishFlat].filter(
    ([key, value]) => sourceLock[key] !== sourceDigest(value),
  )
  const unknownLocks = Object.keys(sourceLock).filter((key) => !englishFlat.has(key))
  const undeclaredEnglish = [...englishFlat].filter(
    ([key, value]) =>
      typeof value === 'string' &&
      value !== '' &&
      translatedFlat.get(key) === value &&
      !allowedEnglish.has(key),
  )
  const unusedEnglishAllowances = [...allowedEnglish].filter(
    (key) => !englishFlat.has(key) || translatedFlat.get(key) !== englishFlat.get(key),
  )
  const emptyTranslations = [...englishFlat].filter(
    ([key, value]) => value !== '' && translatedFlat.get(key) === '',
  )
  const unsafeMarkers = [...translatedFlat].filter(
    ([, value]) => typeof value === 'string' && /HFLPROTECTED|[⟦⟧]/.test(value),
  )
  const reviewFailures = [
    ['stale source locks', stale.map(([key]) => key)],
    ['unknown source locks', unknownLocks],
    ['undeclared English translations', undeclaredEnglish.map(([key]) => key)],
    ['unused English allowances', unusedEnglishAllowances],
    ['empty translations', emptyTranslations.map(([key]) => key)],
    ['unsafe migration markers', unsafeMarkers.map(([key]) => key)],
  ].filter(([, values]) => values.length)
  if (reviewFailures.length) {
    const lines = reviewFailures.flatMap(([label, values]) => [
      `${label} (${values.length}):`,
      ...values.slice(0, 50).map((key) => `  - ${key}`),
      ...(values.length > 50 ? [`  ... and ${values.length - 50} more`] : []),
    ])
    throw new Error(`translation review contract failed\n${lines.join('\n')}`)
  }

  const frontendDir = path.join(outputDir, 'frontend')
  fs.mkdirSync(frontendDir, { recursive: true })
  fs.writeFileSync(path.join(frontendDir, 'messages.json'), `${JSON.stringify(messages)}\n`)
  fs.writeFileSync(path.join(frontendDir, 'element-plus.json'), `${JSON.stringify(componentLocale)}\n`)
  fs.writeFileSync(
    path.join(outputDir, 'manifest.json'),
    `${JSON.stringify({
      schema: 2,
      id: definition.id,
      display_name: definition.display_name,
      version,
      compatible_app: `==${version}`,
      frontend_code: definition.frontend_code,
      backend_code: definition.backend_code,
      aliases: definition.aliases,
      component_locale: definition.component_locale,
    }, null, 2)}\n`,
  )
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true })
}
