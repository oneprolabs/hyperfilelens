import type { HomeLocale } from './homeCopy'

/**
 * Who wrote a piece, and what relationship they have to HyperFileLens.
 *
 * Nothing in the UI reads this — cards show the byline, the platform and the
 * date, and claim nothing about a piece being independent. It is recorded so
 * the index stays truthful about its own contents: if an article was written by
 * someone working on the project, `team` is the honest value no matter which
 * account published it. See `.vitepress/data/README.md`.
 */
export type BlogAffiliation = 'official' | 'team' | 'community'

export interface BlogEntry {
  /** `post` lives on this site; `link` points at another platform. */
  kind: 'post' | 'link'
  title: string
  url: string
  /** ISO date (YYYY-MM-DD). */
  date: string
  summary: string
  author?: string
  /** Platform an external piece was published on, e.g. "Medium". */
  source?: string
  affiliation: BlogAffiliation
  tags: string[]
}

export interface ExternalEntry {
  title: string
  url: string
  source: string
  author: string
  /** Record-keeping only — nothing in the UI reads it. See data/README.md. */
  affiliation?: Exclude<BlogAffiliation, 'official'>
  date: string
  summary: string
  tags?: string[]
}

export interface LoadedPost {
  url: string
  locale: string
  title: string
  date: string
  summary: string
  author: string
  tags: string[]
}

export function externalToEntry(item: ExternalEntry): BlogEntry {
  return {
    kind: 'link',
    title: item.title,
    url: item.url,
    date: item.date,
    summary: item.summary,
    author: item.author,
    source: item.source,
    affiliation: item.affiliation ?? 'community',
    tags: item.tags ?? [],
  }
}

export function postToEntry(post: LoadedPost): BlogEntry {
  return {
    kind: 'post',
    title: post.title,
    url: post.url,
    date: post.date,
    summary: post.summary,
    author: post.author,
    affiliation: 'official',
    tags: post.tags,
  }
}

/** Newest first; ties broken by title so the order is stable across builds. */
export function byDateDesc(a: BlogEntry, b: BlogEntry): number {
  return b.date.localeCompare(a.date) || a.title.localeCompare(b.title)
}

/**
 * Collapses the same article appearing more than once — a cross-post of a piece
 * we already host, or one third-party article mirrored onto two platforms. The
 * on-site copy wins so the reader lands on our own page; otherwise the first
 * entry in date order wins. Titles are matched loosely because platforms differ
 * in punctuation and spacing.
 */
function titleKey(title: string): string {
  return title
    .toLowerCase()
    .replace(/[\s\u3000]+/g, '')
    .replace(/[（）()【】[\]「」“”"'’,，。.:：;；!！?？—–\-~～]/g, '')
}

function dedupe(entries: BlogEntry[]): BlogEntry[] {
  const seen = new Map<string, BlogEntry>()
  for (const entry of entries) {
    const key = titleKey(entry.title)
    const kept = seen.get(key)
    if (!kept || (kept.kind === 'link' && entry.kind === 'post')) seen.set(key, entry)
  }
  return [...seen.values()].sort(byDateDesc)
}

export function buildFeed(posts: LoadedPost[], external: ExternalEntry[], locale: HomeLocale): BlogEntry[] {
  return dedupe([
    ...posts.filter((post) => post.locale === locale).map(postToEntry),
    ...external.map(externalToEntry),
  ].sort(byDateDesc))
}

export function formatDate(iso: string, locale: HomeLocale): string {
  const tags: Record<HomeLocale, string> = { en: 'en-US', zh: 'zh-CN', es: 'es-ES' }
  const parsed = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return iso
  return new Intl.DateTimeFormat(tags[locale], {
    year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC',
  }).format(parsed)
}
