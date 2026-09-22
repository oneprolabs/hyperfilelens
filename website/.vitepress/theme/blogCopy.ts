import type { HomeLocale } from './homeCopy'

/**
 * The feed does not label who wrote what. Every card still carries the real
 * byline, the platform it was published on, and an outbound link, and the lead
 * says the list mixes our own writing with articles published elsewhere — so
 * nothing here claims a piece is independent. Disclosure of any commercial
 * relationship belongs on the articles themselves, on the platform that hosts
 * them, which is also where the platforms' own rules apply.
 */
export interface BlogCopy {
  kicker: string
  title: string
  lead: string
  empty: string
  readMore: string
  readOn: (source: string) => string
  homeKicker: string
  homeTitle: string
  homeViewAll: string
  backToBlog: string
  publishedOn: string
}

const en: BlogCopy = {
  kicker: 'Blog',
  title: 'Engineering notes on backup and agents.',
  lead: 'Writing from the HyperFileLens and SourceLens team, plus articles about the projects published on other platforms.',
  empty: 'Nothing here yet.',
  readMore: 'Read more',
  readOn: (source: string) => `Read on ${source}`,
  homeKicker: 'Blog',
  homeTitle: 'Latest writing',
  homeViewAll: 'View all posts',
  backToBlog: 'All posts',
  publishedOn: 'Originally published on',
}

const zh: BlogCopy = {
  kicker: '博客',
  title: '备份与 Agent 的工程笔记',
  lead: 'HyperFileLens 与 SourceLens 团队的文章，以及发表在其他平台上的相关内容。',
  empty: '暂时还没有内容。',
  readMore: '阅读全文',
  readOn: (source: string) => `在 ${source} 上阅读`,
  homeKicker: '博客',
  homeTitle: '最新文章',
  homeViewAll: '查看全部文章',
  backToBlog: '全部文章',
  publishedOn: '原文首发于',
}

const es: BlogCopy = {
  kicker: 'Blog',
  title: 'Notas de ingeniería sobre backup y agentes.',
  lead: 'Artículos del equipo de HyperFileLens y SourceLens, además de contenido sobre los proyectos publicado en otras plataformas.',
  empty: 'Todavía no hay nada aquí.',
  readMore: 'Leer más',
  readOn: (source: string) => `Leer en ${source}`,
  homeKicker: 'Blog',
  homeTitle: 'Publicaciones recientes',
  homeViewAll: 'Ver todas las publicaciones',
  backToBlog: 'Todas las publicaciones',
  publishedOn: 'Publicado originalmente en',
}

export const blogCopy: Record<HomeLocale, BlogCopy> = { en, zh, es }
