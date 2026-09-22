import { createContentLoader } from 'vitepress'
import type { LoadedPost } from '../theme/blog'

declare const data: LoadedPost[]
export { data }

/**
 * Content loader URLs keep the source directory prefix (/en/blog/…) because the
 * function form of `rewrites` is not applied here. Recover the locale from that
 * prefix, then drop it for `en` so the href matches the page VitePress emits.
 */
function resolveUrl(url: string): { locale: string; url: string } {
  const match = url.match(/^\/(en|zh|es)\/blog\//)
  const locale = match ? match[1] : 'en'
  return { locale, url: locale === 'en' ? url.replace(/^\/en\//, '/') : url }
}

function toIsoDate(value: unknown): string {
  if (value instanceof Date) return value.toISOString().slice(0, 10)
  if (typeof value === 'string') return value.slice(0, 10)
  return ''
}

export default createContentLoader('*/blog/posts/*.md', {
  transform(raw): LoadedPost[] {
    return raw
      .filter(({ frontmatter }) => frontmatter.title && !frontmatter.draft)
      .map(({ url, frontmatter }) => ({
        ...resolveUrl(url),
        title: frontmatter.title as string,
        date: toIsoDate(frontmatter.date),
        summary: (frontmatter.description ?? '') as string,
        author: (frontmatter.author ?? '') as string,
        tags: (frontmatter.tags ?? []) as string[],
      }))
      .sort((a, b) => b.date.localeCompare(a.date) || a.title.localeCompare(b.title))
  },
})
