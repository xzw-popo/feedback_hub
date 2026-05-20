import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('@/views/Overview.vue') },
  { path: '/list', name: 'list', component: () => import('@/views/List.vue') },
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
