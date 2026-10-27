import type { Component } from 'vue'
import { AtSign, Globe, Mail } from 'lucide-vue-next'
import DingTalkMark from '../components/ops/DingTalkMark.vue'
import WeComMark from '../components/ops/WeComMark.vue'
import type { ChannelType } from '../pages/ops/notificationTypes'

export const notificationTypeIcons: Record<ChannelType, Component> = {
  email: Mail,
  webhook: Globe,
  dingtalk: DingTalkMark,
  wecom: WeComMark,
  sms: AtSign,
}

export function getNotificationTypeIcon(type: ChannelType | string | undefined | null): Component | null {
  if (type && type in notificationTypeIcons) {
    return notificationTypeIcons[type as ChannelType]
  }
  return null
}
