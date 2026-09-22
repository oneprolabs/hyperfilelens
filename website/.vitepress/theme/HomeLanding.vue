<script setup lang="ts">
import { computed, ref } from 'vue'
import IconSprite from './IconSprite.vue'
import SiteHeader from './SiteHeader.vue'
import SiteFooter from './SiteFooter.vue'
import HomeBlogSection from './HomeBlogSection.vue'
import { homeCopy } from './homeCopy'
import { useSiteLocale } from './useSiteLocale'
import { githubUrl, sourceLensUrl, useAppOrigin } from './useAppOrigin'

const locale = useSiteLocale()
const copy = computed(() => homeCopy[locale.value])
const installCommand = computed(() => copy.value.openSource.terminal.installCommand)
const { loginUrl, openApp } = useAppOrigin()

const copied = ref(false)
let copyResetTimer: number | undefined

async function copyInstallCommand() {
  try {
    const command = installCommand.value
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(command)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = command
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

const cardAccents = ['violet', '', 'amber'] as const
</script>

<template>
  <div class="hfl-site">
    <IconSprite />

    <SiteHeader :locale="locale" />

    <main>
      <section class="hero" aria-labelledby="hero-title">
        <div class="hero-backdrop" aria-hidden="true"></div>
        <div class="hero-copy">
          <a class="open-source-pill" :href="githubUrl" target="_blank" rel="noopener noreferrer">
            <svg aria-hidden="true"><use href="#icon-github" /></svg>
            {{ copy.hero.pill }}
            <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
          </a>
          <h1 id="hero-title">{{ copy.hero.titleLead }}<br /><span>{{ copy.hero.titleAccent }}</span></h1>
          <p class="hero-lead">{{ copy.hero.lead }}</p>
          <div class="hero-actions">
            <a class="button button-primary" :href="loginUrl" target="_blank" rel="noopener noreferrer" @click="openApp($event, 'hero')">
              <img src="/brand/icons/hyperfilelens-mark-32.png" alt="" class="button-logo" />
              {{ copy.hero.primaryCta }}
              <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
              <span class="free-badge">{{ copy.hero.freeBadge }}</span>
            </a>
            <a class="button button-secondary" :href="githubUrl" target="_blank" rel="noopener noreferrer">
              <svg aria-hidden="true"><use href="#icon-github" /></svg>
              {{ copy.hero.secondaryCta }}
            </a>
          </div>
          <div class="hero-proof" :aria-label="copy.hero.proofAria">
            <span v-for="item in copy.hero.proof" :key="item"><svg aria-hidden="true"><use href="#icon-check" /></svg>{{ item }}</span>
          </div>
        </div>

        <div class="product-stage" :aria-label="copy.hero.stageAria">
          <img
            class="product-shot"
            src="/product-overview.webp"
            width="1672"
            height="941"
            :alt="copy.hero.shotAlt"
          />
        </div>
      </section>

      <section id="use-cases" class="section-block use-cases" aria-labelledby="use-cases-title">
        <div class="section-heading centered">
          <p class="section-kicker">{{ copy.useCases.kicker }}</p>
          <h2 id="use-cases-title">{{ copy.useCases.title }}</h2>
        </div>
        <div class="use-case-grid">
          <article v-for="(card, index) in copy.useCases.cards" :key="card.title">
            <div class="use-case-head">
              <div class="card-icon" :class="cardAccents[index]">
                <svg aria-hidden="true"><use :href="['#icon-search', '#icon-network', '#icon-code'][index]" /></svg>
              </div>
              <h3>{{ card.title }}</h3>
            </div>
            <p>{{ card.body }}</p>
          </article>
          <p class="use-case-divider">{{ copy.useCases.divider }}</p>
          <article v-for="(card, index) in copy.useCases.moreCards" :key="card.title">
            <div class="use-case-head">
              <div class="card-icon" :class="['', 'violet', 'amber'][index]">
                <svg aria-hidden="true"><use :href="['#icon-server', '#icon-network', '#icon-restore'][index]" /></svg>
              </div>
              <h3>{{ card.title }}</h3>
            </div>
            <p>{{ card.body }}</p>
          </article>
        </div>
      </section>

      <section id="how-it-works" class="section-block how-it-works" aria-labelledby="flow-title">
        <div class="section-heading centered">
          <p class="section-kicker">{{ copy.howItWorks.kicker }}</p>
          <h2 id="flow-title">{{ copy.howItWorks.title }}</h2>
          <p>{{ copy.howItWorks.lead }}</p>
        </div>
        <div class="engine-diagram-frame">
          <img
            class="engine-diagram"
            src="/how-it-works.webp"
            width="1600"
            height="878"
            loading="lazy"
            :alt="copy.howItWorks.diagramAlt"
          />
        </div>
      </section>

      <section id="open-source" class="open-source-section" aria-labelledby="open-source-title">
        <div class="open-source-grid">
          <div class="open-source-copy">
            <p class="section-kicker dark-kicker">{{ copy.openSource.kicker }}</p>
            <h2 id="open-source-title">
              <template v-for="(line, index) in copy.openSource.titleLines" :key="line">
                <br v-if="index > 0" />{{ line }}
              </template>
            </h2>
            <p>{{ copy.openSource.lead }}</p>
            <div class="open-source-callout">
              <svg aria-hidden="true"><use href="#icon-check" /></svg>
              <p>{{ copy.openSource.callout }}</p>
            </div>
            <div class="open-source-actions">
              <a class="button button-light" :href="githubUrl" target="_blank" rel="noopener noreferrer"><svg aria-hidden="true"><use href="#icon-github" /></svg>{{ copy.openSource.githubCta }}</a>
              <a class="button button-dark-outline" :href="githubUrl" target="_blank" rel="noopener noreferrer">{{ copy.openSource.starCta }}</a>
            </div>
            <div class="open-source-links">
              <a class="source-link" :href="sourceLensUrl" target="_blank" rel="noopener noreferrer"><svg aria-hidden="true"><use href="#icon-github" /></svg>{{ copy.openSource.engineRepo }}</a>
              <a class="source-link" :href="copy.openSource.installGuideHref" target="_blank" rel="noopener noreferrer">{{ copy.openSource.installGuide }} <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
            </div>
            <p class="beta-note">{{ copy.openSource.betaNote }}</p>
          </div>
          <div class="terminal-card" :aria-label="copy.openSource.terminal.aria">
            <div class="terminal-bar">
              <span class="terminal-lights" aria-hidden="true"><i></i><i></i><i></i></span>
              <div class="terminal-title"><strong>{{ copy.openSource.terminal.title }}</strong><span>{{ copy.openSource.terminal.subtitle }}</span></div>
              <button
                type="button"
                class="copy-command"
                :aria-label="copied ? copy.openSource.terminal.copied : copy.openSource.terminal.copy"
                @click="copyInstallCommand"
              >
                <svg aria-hidden="true"><use :href="copied ? '#icon-check' : '#icon-copy'" /></svg>
                <span>{{ copied ? copy.openSource.terminal.copied : copy.openSource.terminal.copy }}</span>
              </button>
            </div>
            <div class="terminal-body">
              <p class="terminal-context">{{ copy.openSource.terminal.context }}</p>
              <pre><code><span class="terminal-comment">{{ copy.openSource.terminal.comment }}</span>
<span class="terminal-prompt">$</span> {{ installCommand }}</code></pre>
            </div>
            <div class="terminal-summary">
              <strong>{{ copy.openSource.terminal.summaryTitle }}</strong>
              <span>{{ copy.openSource.terminal.summarySteps }}</span>
            </div>
          </div>
        </div>
      </section>

      <HomeBlogSection :locale="locale" />

      <section id="contact" class="section-block contact-section" aria-labelledby="contact-title">
        <div class="section-heading centered">
          <p class="section-kicker">{{ copy.contact.kicker }}</p>
          <h2 id="contact-title">{{ copy.contact.title }}</h2>
        </div>
        <div class="contact-grid">
          <article>
            <div class="use-case-head">
              <div class="card-icon"><svg aria-hidden="true"><use href="#icon-building" /></svg></div>
              <h3>{{ copy.contact.enterprise.title }}</h3>
            </div>
            <p>{{ copy.contact.enterprise.body }}</p>
            <a class="text-link" href="mailto:oneprolabs@oneprocloud.com">{{ copy.contact.enterprise.linkLabel }} <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
          </article>
          <article>
            <div class="use-case-head">
              <div class="card-icon violet"><svg aria-hidden="true"><use href="#icon-github" /></svg></div>
              <h3>{{ copy.contact.support.title }}</h3>
            </div>
            <p>{{ copy.contact.support.body }}</p>
            <a class="text-link" :href="`${githubUrl}/issues`" target="_blank" rel="noopener noreferrer">{{ copy.contact.support.linkLabel }} <svg aria-hidden="true"><use href="#icon-arrow" /></svg></a>
          </article>
        </div>
      </section>

    </main>

    <SiteFooter :locale="locale" />
  </div>
</template>
