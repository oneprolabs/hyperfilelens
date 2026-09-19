import { lazyRoute } from '../router/lazyRoute'

/** Host-owned governance pages shared by Community and Enterprise. */
export const governanceRoutes = [
  {
    path: 'node/subscription',
    component: lazyRoute(() => import('../pages/settings/Subscription.vue')),
  },
]
