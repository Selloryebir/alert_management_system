<script setup lang="ts">
import { computed, ref } from "vue";

import {
  AUDIT_EVENT_TYPES,
  DEMO_OPERATOR,
  exportReport,
  fetchAuditEvents,
  resetDemo,
  type AuditPage,
} from "./operations";

const props = defineProps<{ runId?: string }>();
const emit = defineEmits<{ demoReset: [] }>();

const AUDIT_PAGE_SIZE = 50;
const reportOperator = ref("");
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
  const operator = reportOperator.value.trim();
  if (!operator) {
    reportError.value = "请填写报告导出操作者后再下载。";
    return;
  }
  if (!props.runId) {
    reportError.value = "请先完成并加载一次分析，再导出整次分析报告。";
    return;
  }
  reportBusy.value = true;
  try {
    const report = await exportReport(props.runId, format, operator);
    const url = URL.createObjectURL(report.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = report.filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    reportMessage.value = `${report.filename} 已下载（${report.blob.size} 字节）。`;
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
    auditPage.value = await fetchAuditEvents(page, AUDIT_PAGE_SIZE, auditFilter.value);
  } catch (error) {
    auditError.value = `审计记录加载失败：${error instanceof Error ? error.message : "未知错误"}。请检查主系统后点击“刷新审计”。`;
  } finally {
    auditBusy.value = false;
  }
}

function detailsText(details: Record<string, unknown>): string {
  return Object.keys(details).length === 0 ? "—" : JSON.stringify(details);
}

async function handleReset() {
  resetError.value = "";
  resetMessage.value = "";
  if (resetConfirmation.value !== "RESET_DEMO") {
    resetError.value = "请输入精确确认值 RESET_DEMO 后再复位。";
    return;
  }
  resetBusy.value = true;
  try {
    const result = await resetDemo(resetConfirmation.value);
    const deleted = Object.entries(result.deleted_counts)
      .map(([table, count]) => `${table} ${count}`)
      .join("、");
    resetMessage.value = `演示数据已复位（${result.completed_at}；${deleted || "业务表均为空"}）。`;
    resetConfirmation.value = "";
    auditPage.value = undefined;
    auditError.value = "";
    reportMessage.value = "";
    reportError.value = "";
    emit("demoReset");
  } catch (error) {
    resetError.value = `演示复位失败：${error instanceof Error ? error.message : "未知错误"}。现有页面状态已保留，请排除进行中的分析或服务故障后重试。`;
  } finally {
    resetBusy.value = false;
  }
}
</script>

<template>
  <section class="review-operations" aria-labelledby="review-operations-title">
    <div class="panel-heading compact-heading">
      <div>
        <p class="eyebrow">M5 · 报告、审计与复位</p>
        <h3 id="review-operations-title">演示运维闭环</h3>
      </div>
      <p class="demo-operator">本地演示身份 <strong>{{ DEMO_OPERATOR }}</strong></p>
    </div>

    <div class="operations-grid">
      <section class="operation-card" aria-labelledby="report-title">
        <h4 id="report-title">整次分析报告</h4>
        <p class="empty-copy">导出完整已完成分析；报告持续标明重建 Demo 与合成数据。</p>
        <label>
          报告操作者（必填）
          <input v-model="reportOperator" data-testid="report-operator" :disabled="reportBusy" />
        </label>
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

      <section class="operation-card" aria-labelledby="audit-title">
        <h4 id="audit-title">只读审计记录</h4>
        <div class="audit-controls">
          <label>
            事件类型
            <select v-model="auditFilter" data-testid="audit-filter" :disabled="auditBusy">
              <option value="">全部</option>
              <option v-for="eventType in AUDIT_EVENT_TYPES" :key="eventType" :value="eventType">{{ eventType }}</option>
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
                <td>{{ event.occurred_at }}</td><td>{{ event.event_type }}</td><td>{{ event.operator }}</td>
                <td>{{ event.target_type }} / {{ event.target_id }}</td><td>{{ event.result }}</td><td class="audit-details">{{ detailsText(event.details) }}</td>
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

    <section class="danger-zone" aria-labelledby="reset-title">
      <h4 id="reset-title">危险操作：复位演示数据</h4>
      <p>这会清空本项目全部导入、分析、处置和审计数据。失败时页面不会伪装成功或清除当前状态。</p>
      <div class="reset-grid">
        <label>
          操作者
          <input :value="DEMO_OPERATOR" data-testid="reset-operator" readonly />
        </label>
        <label>
          输入 RESET_DEMO 精确确认
          <input v-model="resetConfirmation" data-testid="reset-confirmation" :disabled="resetBusy" autocomplete="off" />
        </label>
        <button type="button" class="danger-button" data-testid="reset-button" :disabled="resetBusy" @click="handleReset">
          {{ resetBusy ? "复位中…" : "复位全部演示数据" }}
        </button>
      </div>
      <p v-if="resetError" class="request-error compact-message" role="alert" data-testid="reset-message">{{ resetError }}</p>
      <p v-if="resetMessage" class="import-message" role="status" data-testid="reset-message">{{ resetMessage }}</p>
    </section>
  </section>
</template>
