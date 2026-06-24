import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'feedback-list', component: () => import('@/views/FeedbackList.vue') },
  { path: '/overview', name: 'overview', component: () => import('@/views/Overview.vue') },
  { path: '/list', name: 'list', component: () => import('@/views/List.vue') },
  { path: '/simple', name: 'simple-list', component: () => import('@/views/SimpleList.vue') },
  { path: '/reports', name: 'reports', component: () => import('@/views/Reports.vue') },
  { path: '/weibo', name: 'weibo', component: () => import('@/views/WeiboList.vue') },
  { path: '/weibo/stats', name: 'weibo-stats', component: () => import('@/views/WeiboOverview.vue') },
  { path: '/weibo/list', redirect: '/weibo' },
  {
    path: '/feedback/:id',
    name: 'detail',
    component: () => import('@/views/Detail.vue'),
    props: true,
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFound.vue'),
  },
]

export const router = createRouter({ history: createWebHistory(), routes })
