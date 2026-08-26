<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  trackWebsiteOpenApp,
  type WebsiteOpenAppPlacement,
} from './analytics'
import LanguageSwitcher from './LanguageSwitcher.vue'

const appOrigin = ref('')

function validOrigin(value: string): string {
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return ''
    if (parsed.pathname !== '/' || parsed.search || parsed.hash) return ''
    return parsed.origin
  } catch {
    return ''
  }
}

function directAppOrigin(): string {
  const hostname = window.location.hostname || '127.0.0.1'
  const host = hostname.includes(':') ? `[${hostname}]` : hostname
  return `https://${host}:11443`
}

onMounted(() => {
  appOrigin.value = validOrigin(window.__HFL_WEBSITE_CONFIG__?.appUrl || '') || directAppOrigin()
})

const loginUrl = computed(() => `${appOrigin.value || '#'}${appOrigin.value ? '/login' : ''}`)

const githubUrl = 'https://github.com/HyperBDR/hyperfilelens'
const sourceLensUrl = 'https://github.com/HyperBDR/sourcelens'
const communityInstallGuideUrl = 'https://github.com/oneprolabs/hyperfilelens#community-online-installation'
const installCommand = [
  'curl -fsSL \\',
  '  https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \\',
  '  | sudo bash -s -- --mirror global',
].join('\n')
const copied = ref(false)
let copyResetTimer: number | undefined

async function copyInstallCommand() {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(installCommand)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = installCommand
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const copySucceeded = document.execCommand('copy')
      textarea.remove()
      if (!copySucceeded) throw new Error('Copy command failed')
    }
    copied.value = true
    window.clearTimeout(copyResetTimer)
    copyResetTimer = window.setTimeout(() => { copied.value = false }, 2200)
  } catch {
    copied.value = false
  }
}

function openApp(event: MouseEvent, placement: WebsiteOpenAppPlacement) {
  const target = loginUrl.value
  if (!target || target === '#') return
  if (
    event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  ) {
    trackWebsiteOpenApp(placement)
    return
  }
  event.preventDefault()
  trackWebsiteOpenApp(placement, () => window.location.assign(target))
}
</script>

<template>
  <div class="hfl-site">
    <svg class="icon-sprite" aria-hidden="true">
      <symbol id="icon-arrow" viewBox="0 0 24 24">
        <path d="M5 12h14M13 6l6 6-6 6" />
      </symbol>
      <symbol id="icon-github" viewBox="0 0 24 24">
        <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.86c-2.78.6-3.37-1.18-3.37-1.18-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53 1.03.9 1.53 2.35 1.09 2.92.83.09-.65.35-1.09.64-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.6 9.6 0 0 1 12 6.84a9.6 9.6 0 0 1 2.5.34c1.92-1.3 2.76-1.02 2.76-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.86v2.75c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" fill="currentColor" stroke="none" />
      </symbol>
      <symbol id="icon-shield" viewBox="0 0 24 24">
        <path d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6l-7-3Z" /><path d="m9 12 2 2 4-4" />
      </symbol>
      <symbol id="icon-network" viewBox="0 0 24 24">
        <rect x="9" y="3" width="6" height="6" rx="2" /><rect x="3" y="15" width="6" height="6" rx="2" /><rect x="15" y="15" width="6" height="6" rx="2" /><path d="M12 9v3M6 15v-3h12v3" />
      </symbol>
      <symbol id="icon-search" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" />
      </symbol>
      <symbol id="icon-restore" viewBox="0 0 24 24">
        <path d="M4 8v5h5" /><path d="M5.8 17.2A8 8 0 1 0 4.2 9" /><path d="M12 7v5l3 2" />
      </symbol>
      <symbol id="icon-server" viewBox="0 0 24 24">
        <rect x="3" y="4" width="18" height="6" rx="2" /><rect x="3" y="14" width="18" height="6" rx="2" /><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6" />
      </symbol>
      <symbol id="icon-code" viewBox="0 0 24 24">
        <path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14" />
      </symbol>
      <symbol id="icon-check" viewBox="0 0 24 24">
        <path d="m5 12 4 4L19 6" />
      </symbol>
      <symbol id="icon-copy" viewBox="0 0 24 24">
        <rect x="8" y="8" width="11" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h2" />
      </symbol>
      <symbol id="icon-building" viewBox="0 0 24 24">
        <path d="M4 21V5l8-3 8 3v16M8 8h2M14 8h2M8 12h2M14 12h2M8 16h2M14 16h2M2 21h20" />
      </symbol>
      <symbol id="icon-globe" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.5 4 5.5 4 9s-1.5 6.5-4 9c-2.5-2.5-4-5.5-4-9s1.5-6.5 4-9Z" />
      </symbol>
      <symbol id="icon-chevron" viewBox="0 0 24 24">
        <path d="m6 9 6 6 6-6" />
      </symbol>
    </svg>

    <header class="site-header-wrap">
      <div class="site-header">
        <a class="brand" href="/en/" aria-label="HyperFileLens home">
          <img src="/logo-mark.svg" alt="" width="34" height="34" />
          <span>HyperFileLens</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#use-cases">Use Cases</a>
          <a href="#how-it-works">How It Works</a>
          <a href="/en/docs/">User Docs</a>
          <a href="#open-source">Open Source</a>
          <a href="#contact">Contact</a>
        </nav>
        <div class="header-actions">
          <LanguageSwitcher current="en" />
          <a class="github-link" :href="githubUrl" aria-label="HyperFileLens on GitHub">
            <svg aria-hidden="true"><use href="#icon-github" /></svg>
            <span>GitHub</span>
          </a>
          <a class="header-cta" :href="loginUrl" @click="openApp($event, 'header')">Try free</a>
        </div>
      </div>
    </header>

    <main>
      <section class="hero" aria-labelledby="hero-title">
        <div class="hero-backdrop" aria-hidden="true"></div>
        <div class="hero-copy">
          <a class="open-source-pill" :href="githubUrl">
            <svg aria-hidden="true"><use href="#icon-github" /></svg>
            OneProLabs · Apache 2.0
            <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
          </a>
          <h1 id="hero-title">Your backups know<br /><span>more than you think.</span></h1>
          <p class="hero-lead">
            Ask questions straight from your document backups — PDFs, Word, Excel, PowerPoint, images, Markdown,
            or any other text format — without touching production.
          </p>
          <div class="hero-actions">
            <a class="button button-primary" :href="loginUrl" @click="openApp($event, 'hero')">
              <img src="/logo-mark-white.svg" alt="" class="button-logo" />
              Try HyperFileLens
              <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
              <span class="free-badge">Free</span>
            </a>
            <a class="button button-secondary" :href="githubUrl">
              <svg aria-hidden="true"><use href="#icon-github" /></svg>
              View on GitHub
            </a>
          </div>
          <div class="hero-proof" aria-label="Key product qualities">
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>Data Governance</span>
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>Agentic RAG</span>
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>Agent Harness</span>
            <span><svg aria-hidden="true"><use href="#icon-check" /></svg>Never Disrupts Production</span>
          </div>
        </div>

        <div class="product-stage" aria-label="HyperFileLens control plane preview">
          <img
            class="product-shot"
            src="/product-overview.webp"
            width="1672"
            height="941"
            alt="HyperFileLens Overview dashboard showing the data protection pipeline from production source to isolated target storage to recovery drill, with 452 sources and 428.7 TB protected, all systems healthy, and recovery verification ready."
          />
        </div>
      </section>

      <section id="use-cases" class="section-block use-cases" aria-labelledby="use-cases-title">
        <div class="section-heading centered">
          <p class="section-kicker">Use Cases</p>
          <h2 id="use-cases-title">Answers, not just backup search results.</h2>
        </div>
        <div class="use-case-grid">
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-search" /></svg></div>
              <h3>Customer support Q&A</h3>
            </div>
            <p>Answer customer questions straight from your product docs, PDFs, and slide decks — multimodal understanding reads across formats, so nothing needs to be reformatted first.</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-network" /></svg></div>
              <h3>Company knowledge base</h3>
            </div>
            <p>Turn scattered specs, policies, and decisions — engineering or company-wide — into one knowledge base every team can actually query, not just search and summarize.</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon amber"><svg aria-hidden="true"><use href="#icon-code" /></svg></div>
              <h3>Source-grounded root cause analysis</h3>
            </div>
            <p>Not keyword matching, not log scraping — the agent reads and reasons through your real source code, the way a senior engineer would, until it can confirm the actual root cause.</p>
          </article>
          <p class="use-case-divider">Still a rock-solid backup tool</p>
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-server" /></svg></div>
              <h3>Any host, plus NAS</h3>
            </div>
            <p>Windows, Linux, and Mac — servers and workstations alike — plus NAS shares, all protected under one policy, not three different tools.</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-network" /></svg></div>
              <h3>No storage lock-in</h3>
            </div>
            <p>Object storage or local storage, any S3-compatible provider — your backups aren't tied to one storage vendor.</p>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon amber"><svg aria-hidden="true"><use href="#icon-restore" /></svg></div>
              <h3>Single-file recovery</h3>
            </div>
            <p>Need one file back right now? Browse any snapshot and restore just that file in seconds — no full-volume restore required.</p>
          </article>
        </div>
      </section>

      <section id="how-it-works" class="section-block how-it-works" aria-labelledby="flow-title">
        <div class="section-heading centered">
          <p class="section-kicker">How It Works</p>
          <h2 id="flow-title">Your files stay put. The engine comes to them.</h2>
          <p>HyperFileLens backs up your documents and code into a safe, isolated copy — then SourceLens, our open source Agentic RAG engine, reasons directly over that copy, never touching production.</p>
        </div>
        <div class="engine-diagram-frame">
          <img
            class="engine-diagram"
            src="/how-it-works.webp"
            width="1600"
            height="878"
            loading="lazy"
            alt="Your documents (Word, PDF, Excel, PowerPoint, images, code, markdown) flow into HyperFileLens for open source backup, protection, and flexible storage. HyperFileLens produces an isolated safe copy with no impact on production, which the SourceLens open source AI agent reasons over directly — reading, searching, navigating, and reasoning with no pre-built index — to produce AI insights: customer support Q&A, a company knowledge base, source-grounded root cause analysis, and actionable insights. The whole pipeline is governed and secure: access control, data stays in your control, audit and compliance, enterprise ready. Open source, Apache 2.0."
          />
        </div>
      </section>

      <section id="open-source" class="open-source-section" aria-labelledby="open-source-title">
        <div class="open-source-grid">
          <div class="open-source-copy">
            <p class="section-kicker dark-kicker">Community</p>
            <h2 id="open-source-title">Open source.<br />One-command install.</h2>
            <p>Install the latest Community tag on an Ubuntu host with Docker. The installer verifies its images and assets before changing the host.</p>
            <div class="open-source-callout">
              <svg aria-hidden="true"><use href="#icon-check" /></svg>
              <p>Community is free and open source, with S3-compatible storage and an AI model or API key. Enterprise capabilities will be available in a later release.</p>
            </div>
            <div class="open-source-actions">
              <a class="button button-light" :href="githubUrl"><svg aria-hidden="true"><use href="#icon-github" /></svg>View on GitHub</a>
              <a class="button button-dark-outline" :href="githubUrl">⭐ Star this project</a>
            </div>
            <div class="open-source-links">
              <a class="source-link" :href="sourceLensUrl"><svg aria-hidden="true"><use href="#icon-github" /></svg>AI engine repo</a>
              <a class="source-link" :href="communityInstallGuideUrl">Installation guide <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
            </div>
            <p class="beta-note">HyperFileLens is currently in public beta.</p>
          </div>
          <div class="terminal-card" aria-label="Community online installation command">
            <div class="terminal-bar">
              <span class="terminal-lights" aria-hidden="true"><i></i><i></i><i></i></span>
              <div class="terminal-title"><strong>Community</strong><span>Latest tag</span></div>
              <button
                type="button"
                class="copy-command"
                :aria-label="copied ? 'Installation command copied' : 'Copy installation command'"
                @click="copyInstallCommand"
              >
                <svg aria-hidden="true"><use :href="copied ? '#icon-check' : '#icon-copy'" /></svg>
                <span>{{ copied ? 'Copied' : 'Copy command' }}</span>
              </button>
            </div>
            <div class="terminal-body">
              <p class="terminal-context">Run on an Ubuntu host</p>
              <pre><code><span class="terminal-comment"># Install the latest Community tag</span>
<span class="terminal-prompt">$</span> {{ installCommand }}</code></pre>
            </div>
            <div class="terminal-summary">
              <strong>Installer handles the rest</strong>
              <span>Environment check · Image pull · Service startup</span>
            </div>
          </div>
        </div>
      </section>

      <section id="contact" class="section-block contact-section" aria-labelledby="contact-title">
        <div class="section-heading centered">
          <p class="section-kicker">Contact</p>
          <h2 id="contact-title">Contact us</h2>
        </div>
        <div class="contact-grid">
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-building" /></svg></div>
              <h3>Enterprise deployment</h3>
            </div>
            <p>Planning a private, self-hosted deployment for your organization? We can help you scope it.</p>
            <a class="text-link" href="mailto:oneprolabs@oneprocloud.com">Email us <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-github" /></svg></div>
              <h3>Technical support</h3>
            </div>
            <p>Running into an issue or found a bug? File it on GitHub and we'll take it from there.</p>
            <a class="text-link" :href="`${githubUrl}/issues`">Open an issue <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
          </article>
        </div>
      </section>

    </main>

    <footer>
      <div class="footer-brand">
        <a class="brand" href="/en/"><img src="/logo-mark.svg" alt="" width="32" height="32" /><span>HyperFileLens</span></a>
        <p>Open source backup with agentic AI insight, by OneProLabs.</p>
        <a class="footer-social" :href="githubUrl" aria-label="HyperFileLens on GitHub"><svg aria-hidden="true"><use href="#icon-github" /></svg></a>
      </div>
      <div class="footer-links">
        <div><strong>Product</strong><a href="#use-cases">Use Cases</a><a href="#how-it-works">How It Works</a><a :href="loginUrl" @click="openApp($event, 'footer')">Try free</a></div>
        <div><strong>Open Source</strong><a :href="githubUrl">HyperFileLens</a><a :href="sourceLensUrl">AI engine</a><a :href="`${githubUrl}/releases`">Releases</a></div>
        <div><strong>Contact</strong><a href="mailto:oneprolabs@oneprocloud.com">Enterprise deployment</a><a :href="`${githubUrl}/issues`">Technical support</a></div>
      </div>
      <div class="footer-bottom"><span>© 2026 OneProLabs</span><span>Public beta · Built in the open</span></div>
    </footer>
  </div>
</template>
