<script setup lang="ts">
import { computed, ref } from "vue";

import {
  AUDIT_EVENT_TYPES,
  exportReport,
  fetchAuditEvents,
  resetDemo,
  type AuditPage,
} from "./operations";
import { auditDetails, zh } from "./labels";

const props = defineProps<{
  runId?: string;
  projectId: string;
  canManage?: boolean;
  systemAdmin?: boolean;
}>();
const emit = defineEmits<{ demoReset: []; reportDownloaded: [] }>();

const AUDIT_PAGE_SIZE = 50;
const reportBusy = ref(false);
const reportMessage = ref("");
const reportError = ref("");
const auditFilter = ref("");
const auditBusy = ref(false);
const auditPage = ref<AuditPage>();
const auditError = ref("");
const resetConfirmation = ref("");
const resetBusy = ref(false);
const resetMessage = ref("");
const resetError = ref("");

const auditLastPage = computed(() => {
  if (!auditPage.value || auditPage.value.total === 0) return 0;
  return Math.ceil(auditPage.value.total / auditPage.value.size) - 1;
});

async function downloadReport(format: "pdf" | "xlsx") {
  reportError.value = "";
  reportMessage.value = "";
  if (!props.runId) {
    reportError.value = "请先完成并加载一次分析，再导出整次分析报告。";
    return;
  }
  reportBusy.value = true;
  try {
    const report = await exportReport(props.runId, format);
    const url = URL.createObjectURL(report.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = report.filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    reportMessage.value = `${report.filename} 已下载（${report.blob.size} 字节）。`;
    emit("reportDownloaded");
  } catch (error) {
    reportError.value = `报告导出失败：${error instanceof Error ? error.message : "未知错误"}。请保留当前分析并重试。`;
  } finally {
    reportBusy.value = false;
  }
}

async function loadAudit(page = 0) {
  auditBusy.value = true;
  auditError.value = "";
  try {
    auditPage.value = await fetchAuditEvents(page, AUDIT_PAGE_SIZE, auditFilter.value, props.projectId);
  } catch (error) {
    auditError.value = `审计记录加载失败：${error instanceof Error ? error.message : "未知错误"}。请检查主系统后点击“刷新审计”。`;
  } finally {
    auditBusy.value = false;
  }
}

async function handleReset() {
  resetError.value = "";
  resetMessage.value = "";
  if (resetConfirmation.value !== "RESET_DATA") {
    resetError.value = "请输入精确确认值 RESET_DATA 后再重置。";
    return;
  }
  resetBusy.value = true;
  try {
    const result = await resetDemo("RESET_DEMO");
    const deleted = Object.values(result.deleted_counts).reduce((total, count) => total + count, 0);
    resetMessage.value = `业务数据已重置（${result.completed_at}；共清理 ${deleted} 条业务与审计记录）。`;
    resetConfirmation.value = "";
    auditPage.value = undefined;
    auditError.value = "";
    reportMessage.value = "";
    reportError.value = "";
    emit("demoReset");
  } catch (error) {
    resetError.value = `业务数据重置失败：${error instanceof Error ? error.message : "未知错误"}。现有页面状态已保留，请排除进行中的分析或服务故障后重试。`;
  } finally {
    resetBusy.value = false;
  }
}
</script>

<template>
  <section class="review-operations" aria-labelledby="review-operations-title">
    <div class="panel-heading compact-heading">
      <div>
        <p class="eyebrow">报告与追溯</p>
        <h3 id="review-operations-title">分析成果与全程追溯</h3>
      </div>
      <p class="operator-badge">操作身份取自当前登录账号</p>
    </div>

    <div class="operations-grid">
      <section class="operation-card" aria-labelledby="report-title">
        <h4 id="report-title">整次分析报告</h4>
        <p class="empty-copy">导出完整分析成果，沉淀趋势、分类、处置、关联链路与审计记录。</p>
        <div class="operation-actions">
          <button type="button" data-testid="report-pdf" :disabled="reportBusy || !runId" @click="downloadReport('pdf')">
            {{ reportBusy ? "生成中…" : "下载 PDF" }}
          </button>
          <button type="button" class="secondary-button" data-testid="report-xlsx" :disabled="reportBusy || !runId" @click="downloadReport('xlsx')">
            {{ reportBusy ? "生成中…" : "下载 XLSX" }}
          </button>
        </div>
        <p v-if="!runId" class="empty-copy">请先完成并加载一次分析。</p>
        <p v-if="reportError" class="request-error compact-message" role="alert" data-testid="report-message">{{ reportError }}</p>
        <p v-if="reportMessage" class="import-message" role="status" data-testid="report-message">{{ reportMessage }}</p>
      </section>

      <section v-if="canManage" class="operation-card" aria-labelledby="audit-title">
        <h4 id="audit-title">只读审计记录</h4>
        <div class="audit-controls">
          <label>
            事件类型
            <select v-model="auditFilter" data-testid="audit-filter" :disabled="auditBusy">
              <option value="">全部</option>
              <option v-for="eventType in AUDIT_EVENT_TYPES" :key="eventType" :value="eventType">{{ zh(eventType) }}</option>
            </select>
          </label>
          <button type="button" data-testid="audit-refresh" :disabled="auditBusy" @click="loadAudit(0)">
            {{ auditBusy ? "加载中…" : "刷新审计" }}
          </button>
        </div>
        <p v-if="auditError" class="request-error compact-message" role="alert">{{ auditError }}</p>
        <p v-if="auditPage && auditPage.items.length === 0" class="empty-copy" data-testid="audit-empty">
          当前条件下没有审计记录。
        </p>
        <div v-else-if="auditPage" class="table-wrap" data-testid="audit-table">
          <table>
            <caption>共 {{ auditPage.total }} 条，第 {{ auditPage.page + 1 }} 页</caption>
            <thead><tr><th>时间</th><th>事件</th><th>操作者</th><th>目标</th><th>结果</th><th>详情</th></tr></thead>
            <tbody>
              <tr v-for="event in auditPage.items" :key="event.event_id">
                <td>{{ event.occurred_at }}</td><td>{{ zh(event.event_type) }}</td><td>{{ event.operator }}</td>
                <td>{{ zh(event.target_type) }} / {{ event.target_id }}</td><td>{{ zh(event.result) }}</td><td class="audit-details">{{ auditDetails(event.details) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="pagination">
            <button type="button" class="secondary-button" :disabled="auditBusy || auditPage.page <= 0" @click="loadAudit(auditPage.page - 1)">上一页</button>
            <span>{{ auditPage.page + 1 }} / {{ auditLastPage + 1 }}</span>
            <button type="button" class="secondary-button" :disabled="auditBusy || auditPage.page >= auditLastPage" @click="loadAudit(auditPage.page + 1)">下一页</button>
          </div>
        </div>
      </section>
    </div>

    <section v-if="systemAdmin" class="danger-zone" aria-labelledby="reset-title">
      <h4 id="reset-title">危险操作：重置全部业务数据</h4>
      <p>这会清空系统内全部项目的导入、分析、处置和审计数据，并恢复初始项目，而不只是当前项目。失败时页面不会清除当前状态。</p>
      <div class="reset-grid">
        <label>
          输入 RESET_DATA 精确确认
          <input v-model="resetConfirmation" data-testid="reset-confirmation" :disabled="resetBusy" autocomplete="off" />
        </label>
        <button type="button" class="danger-button" data-testid="reset-button" :disabled="resetBusy" @click="handleReset">
          {{ resetBusy ? "重置中…" : "重置全部业务数据" }}
        </button>
      </div>
      <p v-if="resetError" class="request-error compact-message" role="alert" data-testid="reset-message">{{ resetError }}</p>
      <p v-if="resetMessage" class="import-message" role="status" data-testid="reset-message">{{ resetMessage }}</p>
    </section>
  </section>
</template>
