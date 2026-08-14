<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { ArrowLeft } from 'lucide-vue-next'
import {
  buildNotificationChannelConfig,
  defaultNotificationChannelForm,
  loadNotificationChannelConfig,
} from '../../composables/useNotificationChannelForm'
import { useNotificationLabels } from '../../composables/useNotificationLabels'
import { getNotificationTypeIcon } from '../../composables/useNotificationTypeIcon'
import { apiErrorMessageI18n } from '../../lib/api'
import {
  createChannel,
  getChannel,
  testChannel,
  updateChannel,
} from '../../lib/notificationApi'
import type { ChannelType } from './notificationTypes'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { channelTypeLabel } = useNotificationLabels()

const saving = ref(false)
const testing = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const formRef = ref<FormInstance | null>(null)
const form = reactive(defaultNotificationChannelForm())

const editingId = computed(() => {
  const id = route.params.id
  if (typeof id === 'string' && id) return Number(id)
  return null
})

const pageTitle = computed(() =>
  editingId.value
    ? t('ops.notification.modalTitleEdit')
    : t('ops.notification.modalTitleCreate'),
)

const typeOptions = computed(() => {
  const base = [
    { value: 'email' as ChannelType, label: t('ops.notification.typeEmail') },
    { value: 'webhook' as ChannelType, label: t('ops.notification.typeWebhook') },
    { value: 'dingtalk' as ChannelType, label: t('ops.notification.typeDingtalk') },
    { value: 'wecom' as ChannelType, label: t('ops.notification.typeWecom') },
  ]
  if (form.type === 'sms') {
    base.push({ value: 'sms', label: t('ops.notification.typeSms') })
  }
  return base
})

const emailEncryptionOptions = computed(() => [
  { value: 'starttls', label: t('ops.notification.emailEncryptionStarttls') },
  { value: 'ssl', label: t('ops.notification.emailEncryptionSsl') },
  { value: 'none', label: t('ops.notification.emailEncryptionNone') },
])

const requiredStringRule = (message: string) => [
  {
    required: true,
    validator: (_rule: unknown, value: unknown, callback: (error?: Error) => void) => {
      if (String(value || '').trim()) {
        callback()
        return
      }
      callback(new Error(message))
    },
    trigger: ['blur', 'change'],
  },
]

const formRules = computed<FormRules>(() => ({
  name: requiredStringRule(t('ops.notification.validateChannelNameRequired')),
  'email.smtp_host': requiredStringRule(t('ops.notification.validateSmtpHostRequired')),
  'email.smtp_port': requiredStringRule(t('ops.notification.validateSmtpPortRequired')),
  'email.from_email': requiredStringRule(t('ops.notification.validateFromEmailRequired')),
  'email.to_emails': requiredStringRule(t('ops.notification.validateRecipientsRequired')),
  'webhook.url': requiredStringRule(t('ops.notification.validateWebhookUrlRequired')),
  'dingtalk.webhook_url': requiredStringRule(t('ops.notification.validateWebhookUrlRequired')),
  'sms.url': requiredStringRule(t('ops.notification.validateWebhookUrlRequired')),
  'sms.phone_numbers': requiredStringRule(t('ops.notification.validateRecipientsRequired')),
  'wecom.webhook_url': requiredStringRule(t('ops.notification.validateWebhookUrlRequired')),
}))

const previewTypeIcon = computed(() => getNotificationTypeIcon(form.type))

const previewBasicRows = computed(() => [
  { label: t('ops.notification.formChannelType'), value: channelTypeLabel(form.type) },
  {
    label: t('ops.notification.formEnabled'),
    value: form.enabled ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled'),
    badge: true,
    success: form.enabled,
  },
])

const previewTargetRows = computed(() => {
  if (form.type === 'email') {
    return [
      { label: t('ops.notification.emailSmtpHost'), value: form.email.smtp_host, mono: true },
      { label: t('ops.notification.emailSmtpPort'), value: form.email.smtp_port },
      { label: t('ops.notification.emailFrom'), value: form.email.from_email, mono: true },
      { label: t('ops.notification.emailTo'), value: form.email.to_emails },
      {
        label: t('ops.notification.emailEncryption'),
        value: emailEncryptionOptions.value.find((opt) => opt.value === form.email.encryption)?.label || form.email.encryption,
        badge: true,
        success: form.email.encryption !== 'none',
      },
    ]
  }
  if (form.type === 'webhook') {
    return [
      { label: t('ops.notification.formWebhookUrl'), value: form.webhook.url, mono: true },
      { label: t('ops.notification.webhookMethod'), value: form.webhook.method },
      { label: t('ops.notification.webhookTimeout'), value: `${form.webhook.timeout}s` },
    ]
  }
  if (form.type === 'dingtalk') {
    return [
      { label: t('ops.notification.formWebhookUrl'), value: form.dingtalk.webhook_url, mono: true },
      { label: t('ops.notification.formAtAll'), value: form.dingtalk.is_at_all ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled'), badge: true, success: form.dingtalk.is_at_all },
    ]
  }
  if (form.type === 'sms') {
    return [
      { label: t('ops.notification.smsApiUrl'), value: form.sms.url, mono: true },
      { label: t('ops.notification.smsPhoneNumbers'), value: form.sms.phone_numbers },
    ]
  }
  return [
    { label: t('ops.notification.formWebhookUrl'), value: form.wecom.webhook_url, mono: true },
    { label: t('ops.notification.formAtAll'), value: form.wecom.is_at_all ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled'), badge: true, success: form.wecom.is_at_all },
  ]
})

function handleBack() {
  router.push('/ops/channels')
}

function buildConfig() {
  return buildNotificationChannelConfig(form)
}

function locateRequiredField(field: string) {
  void nextTick(() => {
    const root = pageRef.value || document
    const fieldEl = root.querySelector<HTMLElement>(`[data-notification-channel-field="${field}"]`)
    if (!fieldEl) return
    fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => {
      const focusEl = fieldEl.querySelector<HTMLElement>(
        'input:not([type="hidden"]), textarea, button, [tabindex]:not([tabindex="-1"])',
      )
      focusEl?.focus({ preventScroll: true })
    }, 240)
  })
}

function firstMissingField() {
  if (!form.name.trim()) return 'name'
  if (form.type === 'email') {
    if (!form.email.smtp_host.trim()) return 'email.smtp_host'
    if (!form.email.smtp_port.trim()) return 'email.smtp_port'
    if (!form.email.from_email.trim()) return 'email.from_email'
    if (!form.email.to_emails.trim()) return 'email.to_emails'
  }
  if (form.type === 'webhook' && !form.webhook.url.trim()) return 'webhook.url'
  if (form.type === 'dingtalk' && !form.dingtalk.webhook_url.trim()) return 'dingtalk.webhook_url'
  if (form.type === 'wecom' && !form.wecom.webhook_url.trim()) return 'wecom.webhook_url'
  if (form.type === 'sms') {
    if (!form.sms.url.trim()) return 'sms.url'
    if (!form.sms.phone_numbers.trim()) return 'sms.phone_numbers'
  }
  return ''
}

async function validateRequiredFields() {
  try {
    await formRef.value?.validate()
    return true
  } catch {
    const field = firstMissingField()
    if (field) locateRequiredField(field)
    return false
  }
}

async function persistChannel(): Promise<number> {
  const body = {
    name: form.name,
    type: form.type,
    enabled: form.enabled,
    config: buildConfig(),
  }
  if (editingId.value) {
    await updateChannel(editingId.value, body)
    return editingId.value
  }
  const created = await createChannel(body)
  return created.id
}

async function saveChannel() {
  if (!(await validateRequiredFields())) return
  saving.value = true
  try {
    const isEdit = !!editingId.value
    await persistChannel()
    ElMessage.success(
      isEdit ? t('ops.notification.updateSuccess') : t('ops.notification.createSuccess'),
    )
    router.push('/ops/channels')
  } catch (e: unknown) {
    ElMessage.error({
      message: apiErrorMessageI18n(e, t, t('ops.notification.createFailed')),
      grouping: true,
    })
  } finally {
    saving.value = false
  }
}

async function testFromEditor() {
  if (!(await validateRequiredFields())) return
  testing.value = true
  try {
    const channelId = await persistChannel()
    if (!editingId.value) {
      await router.replace(`/ops/channels/${channelId}/edit`)
    }
    const res = await testChannel(channelId)
    const ok = res.status === 'success'
    if (ok) ElMessage.success({ message: t('ops.notification.testSuccess'), grouping: true })
    else ElMessage.error({ message: res.error || t('ops.notification.testFailed'), grouping: true })
  } catch (e: unknown) {
    ElMessage.error({
      message: apiErrorMessageI18n(e, t, t('ops.notification.testFailed')),
      grouping: true,
    })
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  if (editingId.value) {
    try {
      const row = await getChannel(editingId.value)
      form.name = row.name
      form.type = row.type as ChannelType
      form.enabled = row.enabled
      loadNotificationChannelConfig(form, form.type, row.config || {})
    } catch (e: unknown) {
      ElMessage.error({
        message: apiErrorMessageI18n(e, t, t('ops.notification.createFailed')),
        grouping: true,
      })
      handleBack()
    }
  }
})

watch(() => form.type, () => {
  void nextTick(() => formRef.value?.clearValidate())
})
</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen"
  >
    <div class="fullscreen-form-page add-s3-page">
      <div class="fullscreen-form-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          @click="handleBack"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ pageTitle }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('ops.notification.modalSubtitle') }}
          </p>
        </div>
      </div>

      <div class="fullscreen-form-layout">
        <div class="fullscreen-form-main">
          <el-form
            ref="formRef"
            :model="form"
            :rules="formRules"
            class="fullscreen-form-el-form notification-channel-editor-form"
            label-position="top"
            require-asterisk-position="right"
          >
            <div class="fullscreen-form-step-stack">
              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.notification.basicInfo') }}
                </h3>
                <div class="fullscreen-form-grid">
                  <el-form-item
                    :label="t('ops.notification.formChannelName')"
                    prop="name"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="name"
                  >
                    <el-input
                      v-model="form.name"
                      :placeholder="t('ops.notification.phChannelNameEmail')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipChannelName') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.formChannelType')"
                    required
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.type"
                      class="w-full"
                      :disabled="!!editingId"
                    >
                      <el-option
                        v-for="opt in typeOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipChannelType') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.formEnabled')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch
                      v-model="form.enabled"
                      :active-text="t('ops.notification.statusEnabled')"
                      :inactive-text="t('ops.notification.statusDisabled')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipEnabled') }}
                    </p>
                  </el-form-item>
                </div>
              </section>

              <section class="fullscreen-form-card fullscreen-form-section">
                <h3 class="fullscreen-form-section__title">
                  <span class="fullscreen-form-section__indicator" />
                  {{ t('ops.notification.sectionTarget') }}
                </h3>

                <template v-if="form.type === 'email'">
                  <div class="fullscreen-form-grid">
                    <el-form-item
                      :label="t('ops.notification.emailSmtpHost')"
                      prop="email.smtp_host"
                      class="fullscreen-form-item--in-card"
                      data-notification-channel-field="email.smtp_host"
                    >
                      <el-input
                        v-model="form.email.smtp_host"
                        :placeholder="t('ops.notification.phSmtpHost')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipSmtpHost') }}
                      </p>
                    </el-form-item>
                    <el-form-item
                      :label="t('ops.notification.emailSmtpPort')"
                      prop="email.smtp_port"
                      class="fullscreen-form-item--in-card"
                      data-notification-channel-field="email.smtp_port"
                    >
                      <el-input
                        v-model="form.email.smtp_port"
                        :placeholder="t('ops.notification.phSmtpPort')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipSmtpPort') }}
                      </p>
                    </el-form-item>
                    <el-form-item
                      :label="t('ops.notification.emailSmtpUsername')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input
                        v-model="form.email.smtp_username"
                        :placeholder="t('ops.notification.phSmtpUsername')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipSmtpUsername') }}
                      </p>
                    </el-form-item>
                    <el-form-item
                      :label="t('ops.notification.emailSmtpPassword')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input
                        v-model="form.email.smtp_password"
                        type="password"
                        show-password
                        :placeholder="t('ops.notification.phSmtpPassword')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipSmtpPassword') }}
                      </p>
                    </el-form-item>
                  </div>
                  <el-form-item
                    :label="t('ops.notification.emailFrom')"
                    prop="email.from_email"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="email.from_email"
                  >
                    <el-input
                      v-model="form.email.from_email"
                      :placeholder="t('ops.notification.phFromEmail')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipEmailFrom') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.emailSubjectOptional')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-input
                      v-model="form.email.email_subject"
                      :placeholder="t('ops.notification.phEmailSubject')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipEmailSubject') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.emailTo')"
                    prop="email.to_emails"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="email.to_emails"
                  >
                    <el-input
                      v-model="form.email.to_emails"
                      :placeholder="t('ops.notification.phRecipientsComma')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipEmailRecipients') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.emailEncryption')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-select
                      v-model="form.email.encryption"
                      class="w-full"
                    >
                      <el-option
                        v-for="opt in emailEncryptionOptions"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.label"
                      />
                    </el-select>
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipEmailEncryption') }}
                    </p>
                  </el-form-item>
                </template>

                <template v-else-if="form.type === 'webhook'">
                  <el-form-item
                    :label="t('ops.notification.formWebhookUrl')"
                    prop="webhook.url"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="webhook.url"
                  >
                    <el-input
                      v-model="form.webhook.url"
                      :placeholder="t('ops.notification.phWebhookUrl')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipWebhookUrl') }}
                    </p>
                  </el-form-item>
                  <div class="fullscreen-form-grid">
                    <el-form-item
                      :label="t('ops.notification.webhookMethod')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-select
                        v-model="form.webhook.method"
                        class="w-full"
                      >
                        <el-option
                          value="POST"
                          label="POST"
                        />
                        <el-option
                          value="PUT"
                          label="PUT"
                        />
                      </el-select>
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipWebhookMethod') }}
                      </p>
                    </el-form-item>
                    <el-form-item
                      :label="t('ops.notification.webhookTimeout')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input-number
                        v-model="form.webhook.timeout"
                        class="w-full"
                        :min="1"
                        :max="120"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipWebhookTimeout') }}
                      </p>
                    </el-form-item>
                  </div>
                  <el-form-item
                    :label="t('ops.notification.webhookHeaders')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-input
                      v-model="form.webhook.headers"
                      type="textarea"
                      :rows="4"
                      :placeholder="t('ops.notification.phWebhookHeaders')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipWebhookHeaders') }}
                    </p>
                  </el-form-item>
                </template>

                <template v-else-if="form.type === 'dingtalk'">
                  <el-form-item
                    :label="t('ops.notification.formWebhookUrl')"
                    prop="dingtalk.webhook_url"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="dingtalk.webhook_url"
                  >
                    <el-input
                      v-model="form.dingtalk.webhook_url"
                      :placeholder="t('ops.notification.phWebhookUrl')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipWebhookUrl') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.formSecret')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-input
                      v-model="form.dingtalk.secret"
                      type="password"
                      show-password
                      :placeholder="t('ops.notification.phSecret')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipSecret') }}
                    </p>
                  </el-form-item>
                  <div class="fullscreen-form-grid">
                    <el-form-item
                      :label="t('ops.notification.formAtMobiles')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input
                        v-model="form.dingtalk.at_mobiles"
                        :placeholder="t('ops.notification.phAtMobiles')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipDingtalkAtMobiles') }}
                      </p>
                    </el-form-item>
                    <el-form-item
                      :label="t('ops.notification.formAtUserIds')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input
                        v-model="form.dingtalk.at_user_ids"
                        :placeholder="t('ops.notification.phAtUserIds')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipDingtalkAtUserIds') }}
                      </p>
                    </el-form-item>
                  </div>
                  <el-form-item
                    :label="t('ops.notification.formAtAll')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch
                      v-model="form.dingtalk.is_at_all"
                      :active-text="t('ops.notification.statusEnabled')"
                      :inactive-text="t('ops.notification.statusDisabled')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipAtAll') }}
                    </p>
                  </el-form-item>
                </template>

                <template v-else-if="form.type === 'sms'">
                  <el-form-item
                    :label="t('ops.notification.smsApiUrl')"
                    prop="sms.url"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="sms.url"
                  >
                    <el-input
                      v-model="form.sms.url"
                      :placeholder="t('ops.notification.phSmsApiUrl')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipSmsApiUrl') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.smsPhoneNumbers')"
                    prop="sms.phone_numbers"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="sms.phone_numbers"
                  >
                    <el-input
                      v-model="form.sms.phone_numbers"
                      :placeholder="t('ops.notification.phSmsPhoneNumbers')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipSmsPhoneNumbers') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.smsApiKey')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-input
                      v-model="form.sms.api_key"
                      type="password"
                      show-password
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipSmsApiKey') }}
                    </p>
                  </el-form-item>
                  <el-form-item
                    :label="t('ops.notification.smsMessageTemplate')"
                    class="fullscreen-form-item--in-card"
                  >
                    <el-input
                      v-model="form.sms.message_template"
                      :placeholder="t('ops.notification.phSmsMessageTemplate')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipSmsMessageTemplate') }}
                    </p>
                  </el-form-item>
                </template>

                <template v-else>
                  <el-form-item
                    :label="t('ops.notification.formWebhookUrl')"
                    prop="wecom.webhook_url"
                    class="fullscreen-form-item--in-card"
                    data-notification-channel-field="wecom.webhook_url"
                  >
                    <el-input
                      v-model="form.wecom.webhook_url"
                      :placeholder="t('ops.notification.phWebhookUrl')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipWebhookUrl') }}
                    </p>
                  </el-form-item>
                  <div class="fullscreen-form-grid">
                    <el-form-item
                      :label="t('ops.notification.formMentionedList')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input
                        v-model="form.wecom.mentioned_list"
                        :placeholder="t('ops.notification.phMentionedList')"
                        :disabled="form.wecom.is_at_all"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipWecomMentionedList') }}
                      </p>
                    </el-form-item>
                    <el-form-item
                      :label="t('ops.notification.formMentionedMobileList')"
                      class="fullscreen-form-item--in-card"
                    >
                      <el-input
                        v-model="form.wecom.mentioned_mobile_list"
                        :placeholder="t('ops.notification.phAtMobiles')"
                      />
                      <p class="fullscreen-form-field__hint">
                        {{ t('ops.notification.tipWecomMentionedMobileList') }}
                      </p>
                    </el-form-item>
                  </div>
                  <el-form-item
                    :label="t('ops.notification.formAtAll')"
                    class="fullscreen-form-status-item"
                  >
                    <el-switch
                      v-model="form.wecom.is_at_all"
                      :active-text="t('ops.notification.statusEnabled')"
                      :inactive-text="t('ops.notification.statusDisabled')"
                    />
                    <p class="fullscreen-form-field__hint">
                      {{ t('ops.notification.tipAtAll') }}
                    </p>
                  </el-form-item>
                </template>
              </section>
            </div>
          </el-form>

          <div class="fullscreen-form-footer fullscreen-form-action-footer fullscreen-form-footer--split">
            <ElButton
              :loading="testing"
              :disabled="testing"
              @click="testFromEditor"
            >
              {{ t('ops.notification.testConnection') }}
            </ElButton>
            <div class="flex gap-2">
              <ElButton @click="handleBack">
                {{ t('common.cancel') }}
              </ElButton>
              <ElButton
                type="primary"
                :loading="saving"
                :disabled="saving"
                @click="saveChannel"
              >
                {{ t('common.save') }}
              </ElButton>
            </div>
          </div>
        </div>

        <aside class="add-form-preview-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon">
                <component
                  :is="previewTypeIcon"
                  class="add-form-preview-header__cloud"
                  :size="28"
                />
              </div>
              <div class="add-form-preview-header__info">
                <h4 class="add-form-preview-header__name">
                  {{ form.name.trim() || t('ops.notification.previewUnnamed') }}
                </h4>
                <p class="add-form-preview-header__type">
                  {{ channelTypeLabel(form.type) }}
                </p>
              </div>
            </div>

            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.notification.basicInfo') }}
                </h5>
                <div
                  v-for="row in previewBasicRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="row.badge
                      ? (row.success ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted')
                      : { 'add-form-preview-row__value--empty': !row.value }"
                  >
                    {{ row.value || '—' }}
                  </span>
                </div>
              </div>

              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">
                  {{ t('ops.notification.sectionTarget') }}
                </h5>
                <div
                  v-for="row in previewTargetRows"
                  :key="row.label"
                  class="add-form-preview-row"
                >
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="[
                      row.mono && row.value ? 'add-form-preview-row__value--mono' : '',
                      row.badge
                        ? (row.success ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted')
                        : { 'add-form-preview-row__value--empty': !row.value },
                    ]"
                  >
                    {{ row.value || '—' }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
