# Blog data

The blog feed mixes two sources, rendered as one list of cards:

- **On-site posts** — markdown in `<locale>/blog/posts/`, rendered as pages here.
- **External links** — `<locale>/blog/external.json`, articles about
  HyperFileLens and SourceLens that live on other platforms. We store metadata
  and link out; the article body is never copied, so there is no copyright issue
  and nothing competes with the original in search.

Each locale keeps its own external list on purpose — the outside articles worth
showing a Spanish reader are not the ones worth showing a Chinese reader.

## external.json fields

See `external.example.json`. All fields are required except `affiliation` and `tags`.

| field | meaning |
| --- | --- |
| `title` | Headline as published, not a rewrite. |
| `url` | Link on the publishing platform, with tracking params stripped. |
| `source` | Platform name, shown on the card ("CSDN", "稀土掘金", "Medium"). |
| `author` | Byline as published. |
| `date` | ISO `YYYY-MM-DD`. |
| `summary` | One or two sentences, written by us. |
| `affiliation` | `team` or `community`. Optional, record-keeping only. |
| `tags` | Optional. |

## What the cards do and do not say

Cards carry the real byline, the platform, the date, and an outbound link. They
carry no badge claiming a piece is independent or official — the page lead says
the list holds both our own writing and articles published elsewhere, so nothing
here asserts that a given article came from an uninvolved third party.

`affiliation` records which is which for our own reference. Keep it accurate even
though it is not displayed: if an article was written by someone working on the
project, `team` is the honest value regardless of which account published it.
Disclosure of that relationship belongs on the article itself, on the platform
hosting it — that is where readers see it and where the platforms' own rules
about undisclosed commercial affiliation apply.

## Editorial rules

**Do not index a cross-post of something we already host.** If an article exists
under `<locale>/blog/posts/`, the copy someone reposted to CSDN or Zhihu is a
distribution channel, not a separate piece. `buildFeed` collapses duplicate
titles as a safety net (the on-site copy wins), but the list should not rely on
it.

**One entry per article.** When the same third-party article appears on two
platforms, pick the one you want to send readers to.

**Cross-posts of our own writing should point home.** When reposting to a
platform that supports it, mark the post as a repost and link the original on
hyperfilelens.com so the two do not compete in search.
