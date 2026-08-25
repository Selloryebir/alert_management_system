<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import BusinessWorkflow from "./BusinessWorkflow.vue";
import {
  createUnknownHealth,
  fetchHealth,
  type HealthStatus,
  type HealthView,
} from "./health";
import {
  confirmImport,
  listImports,
  previewImport,
  type ImportBatch,
} from "./imports";

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
const selectedFile = ref<File>();
const mappingText = ref("");
const importBusy = ref(false);
const importMessage = ref("");
const currentBatch = ref<ImportBatch>();
const recentBatches = ref<ImportBatch[]>([]);

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

function selectFile(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0];
  currentBatch.value = undefined;
  importMessage.value = "";
}

function parseMapping(): Record<string, string> | undefined {
  if (!mappingText.value.trim()) return undefined;
  const parsed: unknown = JSON.parse(mappingText.value);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("字段映射必须是 JSON 对象");
  }
  if (Object.values(parsed).some((value) => typeof value !== "string")) {
    throw new Error("字段映射的源表头必须是字符串");
  }
  return parsed as Record<string, string>;
}

async function previewSelectedFile() {
  if (!selectedFile.value) {
    importMessage.value = "请先选择 CSV、TXT 或 XLSX 文件。";
    return;
  }
  importBusy.value = true;
  importMessage.value = "";
  try {
    currentBatch.value = await previewImport(selectedFile.value, parseMapping());
  } catch (error) {
    importMessage.value = error instanceof Error ? error.message : "预览失败";
  } finally {
    importBusy.value = false;
  }
}

async function confirmCurrentBatch() {
  if (!currentBatch.value) return;
  importBusy.value = true;
  importMessage.value = "";
  try {
    currentBatch.value = await confirmImport(currentBatch.value.batch_id);
    importMessage.value = `批次 ${currentBatch.value.batch_id} 已导入。`;
  } catch (error) {
    importMessage.value = error instanceof Error ? error.message : "确认导入失败";
  } finally {
    importBusy.value = false;
  }
}

async function refreshBatches() {
  importBusy.value = true;
  importMessage.value = "";
  try {
    recentBatches.value = await listImports();
  } catch (error) {
    importMessage.value = error instanceof Error ? error.message : "批次列表加载失败";
  } finally {
    importBusy.value = false;
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
        从合成文件导入、规则分析、统计查看到人工处置均在本页面完成；规则输出仅供审核演示，不代表已确认工业根因。
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

    <section class="import-panel" aria-labelledby="import-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">M2 · 合成数据导入</p>
          <h2 id="import-title">导入向导</h2>
        </div>
        <button type="button" class="secondary-button" :disabled="importBusy" @click="refreshBatches">
          刷新批次
        </button>
      </div>

      <div class="import-form">
        <label>
          <span>报警文件</span>
          <input data-testid="file-input" type="file" accept=".csv,.txt,.xlsx" :disabled="importBusy" @change="selectFile" />
        </label>
        <label>
          <span>可选字段映射（目标字段到源表头的 JSON）</span>
          <textarea
            v-model="mappingText"
            rows="3"
            :disabled="importBusy"
            placeholder='例如 {"event_time":"发生时间","tag":"位号"}'
          />
        </label>
        <button data-testid="preview-button" type="button" :disabled="importBusy || !selectedFile" @click="previewSelectedFile">
          {{ importBusy ? "处理中…" : "校验并预览" }}
        </button>
      </div>

      <p v-if="importMessage" class="import-message" role="status">{{ importMessage }}</p>

      <article v-if="currentBatch" class="batch-detail" aria-labelledby="batch-title" data-testid="preview-summary">
        <div class="status-line">
          <h3 id="batch-title">{{ currentBatch.file_name }}</h3>
          <span class="status-badge" :class="`batch-${currentBatch.status.toLowerCase()}`">
            {{ currentBatch.status }}
          </span>
        </div>
        <p>
          总行数 {{ currentBatch.total_rows }} · 有效 {{ currentBatch.valid_rows }} · 错误
          {{ currentBatch.error_count }}
        </p>
        <button
          v-if="currentBatch.status === 'READY'"
          type="button"
          data-testid="confirm-import"
          :disabled="importBusy"
          @click="confirmCurrentBatch"
        >
          确认导入
        </button>

        <div v-if="currentBatch.errors.length" class="table-wrap">
          <table>
            <caption>校验错误</caption>
            <thead>
              <tr><th>源行</th><th>字段</th><th>代码</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="error in currentBatch.errors" :key="`${error.source_row}-${error.field}-${error.code}`">
                <td>{{ error.source_row }}</td><td>{{ error.field }}</td><td>{{ error.code }}</td><td>{{ error.message }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="currentBatch.preview_rows.length" class="table-wrap">
          <table>
            <caption>规范化预览（最多 20 行）</caption>
            <thead>
              <tr><th>源行</th><th>时间</th><th>位号</th><th>描述</th><th>优先级</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in currentBatch.preview_rows" :key="row.source_row">
                <td>{{ row.source_row }}</td><td>{{ row.event_time }}</td><td>{{ row.tag }}</td><td>{{ row.description }}</td><td>{{ row.priority }}</td><td>{{ row.state }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <div v-if="recentBatches.length" class="table-wrap recent-batches">
        <table>
          <caption>最近导入批次</caption>
          <thead>
            <tr><th>文件</th><th>格式</th><th>状态</th><th>总行数</th><th>创建时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="batch in recentBatches" :key="batch.batch_id">
              <td>{{ batch.file_name }}</td><td>{{ batch.format }}</td><td>{{ batch.status }}</td><td>{{ batch.total_rows }}</td><td>{{ batch.created_at }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <BusinessWorkflow :current-batch="currentBatch" :batches="recentBatches" />
  </main>
</template>
