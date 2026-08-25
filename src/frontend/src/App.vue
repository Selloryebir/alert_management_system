<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  createUnknownHealth,
  fetchHealth,
  type HealthStatus,
  type HealthView,
} from "./health";

interface StatusItem {
  key: keyof HealthView;
  label: string;
  hint: string;
}

const items: StatusItem[] = [
  {
    key: "system",
    label: "主系统",
    hint: "请查看主系统日志，并确认健康接口可访问。",
  },
  {
    key: "database",
    label: "PostgreSQL",
    hint: "请确认 PostgreSQL 进程已启动且连接配置正确。",
  },
  {
    key: "algorithm",
    label: "算法服务",
    hint: "请确认算法服务已启动，并检查主系统到算法服务的连接。",
  },
];

const health = ref<HealthView>(createUnknownHealth());
const loading = ref(false);
const requestFailed = ref(false);

const statusText: Record<HealthStatus, string> = {
  UP: "UP",
  DOWN: "DOWN",
  UNKNOWN: "UNKNOWN",
};

const summary = computed(() => {
  const statuses = Object.values(health.value).map((component) => component.status);
  if (requestFailed.value) return "无法获取健康状态";
  if (loading.value) return "正在检查服务状态";
  if (statuses.some((status) => status === "DOWN")) return "部分服务不可用";
  if (statuses.some((status) => status === "UNKNOWN")) return "部分状态未知";
  return "所有基础服务正常";
});

function guidance(key: keyof HealthView): string {
  const component = health.value[key];
  const item = items.find((candidate) => candidate.key === key);
  if (component.status === "DOWN") {
    const detail = component.detail ? `接口详情：${component.detail} ` : "";
    return `${detail}${item?.hint ?? "请检查对应服务。"}`;
  }
  if (component.status === "UNKNOWN") {
    const detail = component.detail ? `接口详情：${component.detail} ` : "";
    return `${detail}健康接口未返回该组件状态，请检查接口响应后重试。`;
  }
  return component.detail ? `服务已响应。${component.detail}` : "服务已响应。";
}

async function loadHealth() {
  loading.value = true;
  requestFailed.value = false;
  health.value = createUnknownHealth();
  try {
    health.value = await fetchHealth();
  } catch {
    requestFailed.value = true;
  } finally {
    loading.value = false;
  }
}

onMounted(loadHealth);
</script>

<template>
  <main class="page-shell">
    <section class="identity" aria-labelledby="page-title">
      <p class="eyebrow">报警管理系统</p>
      <h1 id="page-title">2026 年灾后重建 Demo</h1>
      <p class="synthetic-notice">仅使用合成数据</p>
      <p class="identity-copy">
        本页面仅检查演示环境的基础服务连接，不代表历史系统功能已经恢复或通过验收。
      </p>
    </section>

    <section class="status-panel" aria-labelledby="status-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">M1 · 运行状态</p>
          <h2 id="status-title">{{ summary }}</h2>
        </div>
        <button type="button" :disabled="loading" @click="loadHealth">
          {{ loading ? "检查中…" : "重新检查" }}
        </button>
      </div>

      <p v-if="requestFailed" class="request-error" role="alert">
        无法访问主系统健康接口。请确认主系统已启动，然后点击“重新检查”。
      </p>

      <div class="status-grid" aria-live="polite">
        <article v-for="item in items" :key="item.key" class="status-card">
          <div class="status-line">
            <h3>{{ item.label }}</h3>
            <span
              class="status-badge"
              :class="`status-${health[item.key].status.toLowerCase()}`"
              :aria-label="`${item.label}状态 ${statusText[health[item.key].status]}`"
            >
              {{ statusText[health[item.key].status] }}
            </span>
          </div>
          <p>{{ guidance(item.key) }}</p>
        </article>
      </div>
    </section>
  </main>
</template>
