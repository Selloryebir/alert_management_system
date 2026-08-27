<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import AccessManagement from "./AccessManagement.vue";
import AccountPanel from "./AccountPanel.vue";
import BusinessWorkflow from "./BusinessWorkflow.vue";
import DataBackupPanel from "./DataBackupPanel.vue";
import LoginPanel from "./LoginPanel.vue";
import ManualAlarmPanel from "./ManualAlarmPanel.vue";
import ProjectWorkspace from "./ProjectWorkspace.vue";
import { currentUser as fetchCurrentUser, initializeCsrf, type CurrentUser } from "./auth";
import { ApiError, setUnauthorizedHandler } from "./api";
import { createUnknownHealth, fetchHealth, type HealthView } from "./health";
import { fieldLabel, priorityLabel, zh } from "./labels";
import { confirmImport, listImports, previewImport, type ImportBatch, type ImportCorrections } from "./imports";
import type { Project } from "./projects";

const TARGET_FIELDS = [
  ["event_time", true], ["return_time", false], ["ack_time", false], ["site", true],
  ["area", true], ["unit", false], ["tag", true], ["description", true],
  ["priority", true], ["state", true], ["value", false], ["threshold", false],
  ["engineering_unit", false], ["source_system", true], ["operator", false],
] as const;
const healthItems = [
  ["system", "主系统", "请查看主系统日志，并确认健康接口可访问。"],
  ["database", "PostgreSQL", "请确认 PostgreSQL 进程已启动且连接配置正确。"],
  ["algorithm", "算法服务", "请确认算法服务已启动，并检查主系统到算法服务的连接。"],
] as const;

const health = ref<HealthView>(createUnknownHealth());
const authLoading = ref(true);
const authMessage = ref("");
const authenticatedUser = ref<CurrentUser>();
const projectWorkspace = ref<InstanceType<typeof ProjectWorkspace>>();
const loading = ref(false);
const requestFailed = ref(false);
const currentProject = ref<Project>();
const selectedFile = ref<File>();
const fieldMapping = ref<Record<string, string>>({});
const corrections = ref<ImportCorrections>({});
const importBusy = ref(false);
const importMessage = ref("");
const currentBatch = ref<ImportBatch>();
const recentBatches = ref<ImportBatch[]>([]);
const fileInputKey = ref(0);
const analysisCompleted = ref(false);
const dispositionCompleted = ref(false);
const reportDownloaded = ref(false);
const demoResetMessage = ref("");
const importErrorDialog = ref<HTMLDialogElement>();
const previewButton = ref<HTMLButtonElement>();
const correctionTitle = ref<HTMLElement>();
const showImportErrors = ref(false);

const healthSummary = computed(() => {
  const statuses = Object.values(health.value).map((item) => item.status);
  if (requestFailed.value) return "无法获取健康状态";
  if (loading.value) return "正在检查服务状态";
  if (statuses.some((status) => status === "DOWN")) return "部分服务不可用";
  if (statuses.some((status) => status === "UNKNOWN")) return "部分状态未知";
  return "所有基础服务正常";
});
const systemAdmin = computed(() => authenticatedUser.value?.global_role === "SYSTEM_ADMIN");
const canManageProject = computed(() => Boolean(systemAdmin.value || currentProject.value?.project_role === "MANAGER"));
const canOperateProject = computed(() => Boolean(canManageProject.value || currentProject.value?.project_role === "ANALYST"));
const projectWritable = computed(() => currentProject.value?.status === "ACTIVE" && canOperateProject.value);
const mappingHeaders = computed(() => currentBatch.value?.headers ?? []);
const correctableErrors = computed(() => {
  const fields = new Set<string>(TARGET_FIELDS.map(([field]) => field));
  const sourceRows = new Set((currentBatch.value?.source_rows ?? []).map((row) => row.source_row));
  const seen = new Set<string>();
  return (currentBatch.value?.errors ?? []).filter((error) => {
    const key = `${error.source_row}-${error.field}`;
    if (seen.has(key) || !fields.has(error.field) || !sourceRows.has(error.source_row)
      || !currentBatch.value?.mapping?.[error.field]) return false;
    seen.add(key);
    return true;
  });
});
const onboarding = computed(() => [
  { title: "创建或选择项目", done: Boolean(currentProject.value), hint: "先确定本次工作归属。" },
  { title: "选择报警样例文件", done: Boolean(selectedFile.value), hint: "支持 CSV、TXT 和 XLSX。" },
  { title: "完成字段映射与预览", done: Boolean(currentBatch.value), hint: "用中文下拉框核对源表头。" },
  { title: "确认导入", done: Boolean(currentBatch.value && ["IMPORTED", "ANALYZING", "COMPLETED", "FAILED"].includes(currentBatch.value.status)), hint: "整批校验通过后再落库。" },
  { title: "完成分析并查看看板", done: analysisCompleted.value, hint: "分析结果来自当前项目。" },
  { title: "处置报警并下载报告", done: dispositionCompleted.value && reportDownloaded.value, hint: "处置和报告都完成后闭环。" },
]);

function healthGuidance(key: keyof HealthView, hint: string): string {
  const component = health.value[key];
  const detail = component.detail ? `接口详情：${component.detail} ` : "";
  if (component.status === "DOWN") return `${detail}${hint}`;
  if (component.status === "UNKNOWN") return `${detail}健康接口未返回该组件状态，请检查接口响应后重试。`;
  return component.detail ? `服务已响应。${component.detail}` : "服务已响应。";
}

async function loadHealth() {
  loading.value = true;
  requestFailed.value = false;
  health.value = createUnknownHealth();
  try { health.value = await fetchHealth(); }
  catch { requestFailed.value = true; }
  finally { loading.value = false; }
}

function resetProjectBusinessState() {
  selectedFile.value = undefined;
  fieldMapping.value = {};
  corrections.value = {};
  currentBatch.value = undefined;
  recentBatches.value = [];
  importMessage.value = "";
  importBusy.value = false;
  analysisCompleted.value = false;
  dispositionCompleted.value = false;
  reportDownloaded.value = false;
  fileInputKey.value += 1;
}

async function handleProjectSelected(project?: Project) {
  const changed = currentProject.value?.project_id !== project?.project_id;
  currentProject.value = project;
  if (changed) {
    resetProjectBusinessState();
    demoResetMessage.value = "";
  }
  if (project) await refreshBatches(false);
}

function selectFile(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0];
  currentBatch.value = undefined;
  fieldMapping.value = {};
  corrections.value = {};
  importMessage.value = "";
  showImportErrors.value = false;
}

async function openImportErrorDialog() {
  showImportErrors.value = true;
  await nextTick();
  if (importErrorDialog.value && !importErrorDialog.value.open) {
    if (typeof importErrorDialog.value.showModal === "function") importErrorDialog.value.showModal();
    else importErrorDialog.value.setAttribute("open", "");
  }
  importErrorDialog.value?.focus();
}

async function closeImportErrorDialog(returnFocus = true) {
  if (importErrorDialog.value?.open && typeof importErrorDialog.value.close === "function") {
    importErrorDialog.value.close();
  }
  showImportErrors.value = false;
  await nextTick();
  if (returnFocus) previewButton.value?.focus();
}

async function jumpToCorrections() {
  await closeImportErrorDialog(false);
  correctionTitle.value?.focus();
  correctionTitle.value?.scrollIntoView({ block: "center" });
}

function handleImportDialogKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    void closeImportErrorDialog();
  }
}

async function previewSelectedFile() {
  if (!currentProject.value) { importMessage.value = "请先选择当前项目。"; return; }
  if (!selectedFile.value) { importMessage.value = "请先选择 CSV、TXT 或 XLSX 文件。"; return; }
  importBusy.value = true;
  importMessage.value = "";
  try {
    const mapping = Object.fromEntries(Object.entries(fieldMapping.value).filter(([, source]) => source));
    currentBatch.value = await previewImport(selectedFile.value, currentProject.value.project_id, mapping, corrections.value);
    fieldMapping.value = { ...(currentBatch.value.mapping ?? mapping) };
    hydrateCorrections(currentBatch.value);
    importMessage.value = currentBatch.value.status === "READY"
      ? "全文件校验通过，请核对预览后确认导入。"
      : "预览已完成，请根据中文错误和源表头调整映射后重新校验。";
    if (currentBatch.value.status === "REJECTED" && currentBatch.value.errors.length > 0) {
      await openImportErrorDialog();
    }
  } catch (error) {
    importMessage.value = error instanceof Error ? error.message : "预览失败";
  } finally { importBusy.value = false; }
}

function hydrateCorrections(batch: ImportBatch) {
  const next: ImportCorrections = Object.fromEntries(
    Object.entries(batch.corrections ?? corrections.value).map(([row, values]) => [row, { ...values }]),
  );
  for (const error of batch.errors) {
    const source = batch.source_rows?.find((row) => row.source_row === error.source_row);
    const sourceHeader = batch.mapping?.[error.field];
    if (!source || !sourceHeader) continue;
    const rowKey = String(error.source_row);
    next[rowKey] ??= {};
    if (!(error.field in next[rowKey])) next[rowKey][error.field] = source.values[sourceHeader] ?? "";
  }
  corrections.value = next;
}

async function confirmCurrentBatch() {
  if (!currentBatch.value) return;
  importBusy.value = true;
  importMessage.value = "";
  try {
    currentBatch.value = await confirmImport(currentBatch.value.batch_id);
    importMessage.value = `批次 ${currentBatch.value.batch_id} 已导入当前项目。`;
    await refreshBatches(false);
    await projectWorkspace.value?.refreshOverview();
  } catch (error) {
    importMessage.value = error instanceof Error ? error.message : "确认导入失败";
  } finally { importBusy.value = false; }
}

async function refreshBatches(showFeedback = true) {
  if (!currentProject.value) {
    if (showFeedback) importMessage.value = "请先选择项目，再查看该项目批次。";
    return;
  }
  importBusy.value = true;
  if (showFeedback) importMessage.value = "";
  try {
    const batches = await listImports(currentProject.value.project_id);
    if (!Array.isArray(batches)) throw new Error("批次列表响应格式无效");
    recentBatches.value = batches;
  }
  catch (error) { if (showFeedback) importMessage.value = error instanceof Error ? error.message : "批次列表加载失败"; }
  finally { importBusy.value = false; }
}

async function handleManualChanged() {
  await refreshBatches(false);
  await projectWorkspace.value?.refreshOverview();
}

function handleAnalysisCompleted() {
  analysisCompleted.value = true;
  void projectWorkspace.value?.refreshOverview();
}

function handleDispositionCompleted() {
  dispositionCompleted.value = true;
  void projectWorkspace.value?.refreshOverview();
}

async function handleDemoReset() {
  resetProjectBusinessState();
  await projectWorkspace.value?.resetAfterDemoReset();
  demoResetMessage.value = "演示数据已复位，已重新加载默认项目。";
}

function handleUnauthorized() {
  if (authenticatedUser.value) authMessage.value = "登录已失效，请重新登录。";
  authenticatedUser.value = undefined;
  currentProject.value = undefined;
  resetProjectBusinessState();
}

async function initializeIdentity() {
  authLoading.value = true;
  try {
    await initializeCsrf();
    try {
      authenticatedUser.value = await fetchCurrentUser();
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        authMessage.value = error instanceof Error ? error.message : "身份服务暂不可用。";
      }
    }
  } catch (error) {
    authMessage.value = `安全令牌初始化失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally {
    authLoading.value = false;
  }
}

function handleAuthenticated(user: CurrentUser) {
  authenticatedUser.value = user;
  authMessage.value = "";
}

function handleLoggedOut() {
  authenticatedUser.value = undefined;
  currentProject.value = undefined;
  authMessage.value = "已安全退出。";
  resetProjectBusinessState();
  void initializeCsrf();
}

onMounted(() => {
  setUnauthorizedHandler(handleUnauthorized);
  void loadHealth();
  void initializeIdentity();
});
onBeforeUnmount(() => setUnauthorizedHandler(undefined));
</script>

<template>
  <main class="page-shell">
    <header class="identity" aria-labelledby="page-title">
      <p class="eyebrow">工业报警分析与处置</p><h1 id="page-title">报警管理系统</h1>
      <p class="synthetic-notice">仅使用合成数据</p>
      <p class="identity-copy">以项目为工作边界，完成文件导入、规则分析、统计查看、人工处置和报告输出。算法结果是可解释的分析建议，不代表已确认工业根因。</p>
    </header>

    <p v-if="authLoading" class="import-message" role="status">正在初始化安全会话…</p>
    <LoginPanel v-else-if="!authenticatedUser" :message="authMessage" @authenticated="handleAuthenticated" />
    <template v-else>
    <AccountPanel :user="authenticatedUser" @changed="handleAuthenticated" @logged-out="handleLoggedOut" />
    <template v-if="!authenticatedUser.must_change_password">
    <nav class="workspace-nav" aria-label="业务区快捷导航"><a href="#project-workspace">项目</a><a href="#import-workspace">导入</a><a href="#business-workflow">分析与处置</a></nav>
    <ProjectWorkspace ref="projectWorkspace" :system-admin="systemAdmin" @selected="handleProjectSelected" />
    <AccessManagement :user="authenticatedUser" :project="currentProject" />
    <DataBackupPanel v-if="systemAdmin" :user="authenticatedUser" />
    <p v-if="demoResetMessage" class="import-message" role="status" data-testid="reset-message">{{ demoResetMessage }}</p>

    <nav class="onboarding-panel" aria-labelledby="onboarding-title">
      <div><p class="eyebrow">首次使用</p><h2 id="onboarding-title">六步完成一次业务闭环</h2></div>
      <ol class="onboarding-steps">
        <li v-for="(step, index) in onboarding" :key="step.title" :data-testid="`onboarding-step-${index + 1}`" :class="{ done: step.done }">
          <span>{{ step.done ? "✓" : index + 1 }}</span><div><strong>{{ step.title }}</strong><small>{{ step.done ? "已完成" : step.hint }}</small></div>
        </li>
      </ol>
    </nav>

    <details class="status-panel">
      <summary><strong>系统运行状态：</strong>{{ healthSummary }}</summary>
      <div class="panel-heading"><div><p class="eyebrow">运行状态</p><h2>{{ healthSummary }}</h2></div><button type="button" :disabled="loading" @click="loadHealth">{{ loading ? "检查中…" : "重新检查" }}</button></div>
      <p v-if="requestFailed" class="request-error" role="alert">无法访问主系统健康接口。请确认主系统已启动，然后点击“重新检查”。</p>
      <div class="status-grid" aria-live="polite">
        <article v-for="[key, label, hint] in healthItems" :key="key" class="status-card"><div class="status-line"><h3>{{ label }}</h3><span class="status-badge" :class="`status-${health[key].status.toLowerCase()}`" :aria-label="`${label}状态 ${zh(health[key].status)}`">{{ zh(health[key].status) }}</span></div><p>{{ healthGuidance(key, hint) }}</p></article>
      </div>
    </details>

    <section id="import-workspace" class="import-panel" aria-labelledby="import-title">
      <div class="panel-heading"><div><p class="eyebrow">当前项目数据</p><h2 id="import-title">文件导入与字段映射</h2></div><button type="button" class="secondary-button" :disabled="importBusy || !currentProject" @click="refreshBatches()">刷新批次</button></div>
      <p v-if="!currentProject" class="empty-copy">请先在上方创建或选择项目。</p>
      <p v-else-if="!projectWritable" class="archive-notice">“{{ currentProject.name }}”处于归档状态，当前仅可查看历史批次。</p>
      <div class="import-form"><label><span>报警文件</span><input :key="fileInputKey" data-testid="file-input" type="file" accept=".csv,.txt,.xlsx" :disabled="importBusy || !projectWritable" @change="selectFile" /></label><button ref="previewButton" data-testid="preview-button" type="button" :disabled="importBusy || !selectedFile || !projectWritable" @click="previewSelectedFile">{{ importBusy ? "处理中…" : mappingHeaders.length ? "按当前映射重新校验" : "读取表头并预览" }}</button></div>

      <section v-if="mappingHeaders.length" class="mapping-editor" data-testid="mapping-editor" aria-labelledby="mapping-title"><h3 id="mapping-title">字段映射</h3><p>左侧是系统目标字段，右侧选择文件中的源表头；可选字段允许留空。</p><div class="mapping-grid"><label v-for="[field, required] in TARGET_FIELDS" :key="field"><span>{{ fieldLabel(field) }}{{ required ? "（必填）" : "（可选）" }}</span><select v-model="fieldMapping[field]" :disabled="importBusy"><option value="">不映射</option><option v-for="header in mappingHeaders" :key="header" :value="header">{{ header }}</option></select></label></div></section>

      <p v-if="importMessage" class="import-message" role="status">{{ importMessage }}</p>
      <article v-if="currentBatch" class="batch-detail" data-testid="preview-summary">
        <div class="status-line"><h3>{{ currentBatch.file_name }}</h3><span class="status-badge" :class="`batch-${currentBatch.status.toLowerCase()}`">{{ zh(currentBatch.status) }}</span></div><p>总行数 {{ currentBatch.total_rows }} · 有效 {{ currentBatch.valid_rows }} · 错误 {{ currentBatch.error_count }}</p>
        <button v-if="currentBatch.status === 'READY'" type="button" data-testid="confirm-import" :disabled="importBusy" @click="confirmCurrentBatch">确认导入</button>
        <div v-if="currentBatch.errors.length" class="table-wrap"><table><caption>校验错误</caption><thead><tr><th scope="col">源行</th><th scope="col">字段</th><th scope="col">错误类型</th><th scope="col">说明</th></tr></thead><tbody><tr v-for="error in currentBatch.errors" :key="`${error.source_row}-${error.field}-${error.code}`"><td>{{ error.source_row }}</td><td>{{ fieldLabel(error.field) }}</td><td>{{ zh(error.code) }}</td><td>{{ error.message }}</td></tr></tbody></table></div>
        <section v-if="correctableErrors.length" class="correction-editor" aria-labelledby="correction-title">
          <h4 id="correction-title" ref="correctionTitle" tabindex="-1">异常行修正</h4>
          <p>只修正本次校验指出的目标字段；系统会连同原文件重新解析并全量校验，不会跳过其他行。</p>
          <div class="correction-grid">
            <label v-for="error in correctableErrors" :key="`${error.source_row}-${error.field}`" :data-testid="`correction-row-${error.source_row}-${error.field}`">
              第 {{ error.source_row }} 行 {{ fieldLabel(error.field) }}修正值
              <input v-model="corrections[String(error.source_row)][error.field]" :disabled="importBusy" />
            </label>
          </div>
          <button type="button" :disabled="importBusy || !selectedFile" @click="previewSelectedFile">按修正值重新全量校验</button>
        </section>
        <div v-if="currentBatch.preview_rows.length" class="table-wrap"><table><caption>规范化预览（最多 20 行）</caption><thead><tr><th>源行</th><th>时间</th><th>位号</th><th>描述</th><th>优先级</th><th>状态</th></tr></thead><tbody><tr v-for="row in currentBatch.preview_rows" :key="row.source_row"><td>{{ row.source_row }}</td><td>{{ row.event_time }}</td><td>{{ row.tag }}</td><td>{{ row.description }}</td><td>{{ priorityLabel(row.priority ?? '') }}</td><td>{{ zh(row.state) }}</td></tr></tbody></table></div>
      </article>

      <dialog v-if="showImportErrors && currentBatch" ref="importErrorDialog" class="import-error-dialog" aria-labelledby="import-error-title" aria-describedby="import-error-summary" data-testid="import-error-dialog" tabindex="-1" @keydown="handleImportDialogKeydown">
        <div class="dialog-heading"><h3 id="import-error-title">文件校验未通过</h3><button type="button" class="secondary-button" aria-label="关闭校验结果" @click="closeImportErrorDialog()">关闭</button></div>
        <p id="import-error-summary">{{ currentBatch.file_name }} 共 {{ currentBatch.total_rows }} 行，发现 {{ currentBatch.error_count }} 项错误。原文件尚未导入。</p>
        <ul class="dialog-error-list"><li v-for="error in currentBatch.errors.slice(0, 8)" :key="`${error.source_row}-${error.field}-${error.code}`">第 {{ error.source_row }} 行 · {{ fieldLabel(error.field) }}：{{ error.message }}</li></ul>
        <p v-if="currentBatch.errors.length > 8">其余错误请在页面内完整校验错误表查看。</p>
        <div class="dialog-actions"><button v-if="correctableErrors.length" type="button" @click="jumpToCorrections">前往修正</button><button type="button" class="secondary-button" @click="closeImportErrorDialog()">返回导入</button></div>
      </dialog>

      <div v-if="recentBatches.length" class="table-wrap recent-batches"><table><caption>当前项目最近导入批次</caption><thead><tr><th>文件</th><th>格式</th><th>状态</th><th>总行数</th><th>创建时间</th></tr></thead><tbody><tr v-for="batch in recentBatches" :key="batch.batch_id"><td>{{ batch.file_name }}</td><td>{{ zh(batch.format) }}</td><td>{{ zh(batch.status) }}</td><td>{{ batch.total_rows }}</td><td>{{ batch.created_at }}</td></tr></tbody></table></div>
    </section>

    <ManualAlarmPanel v-if="currentProject" :project-id="currentProject.project_id" :site="currentProject.site" :area="currentProject.unit_name" :read-only="!projectWritable" @changed="handleManualChanged" />
    <BusinessWorkflow v-if="currentProject" :key="currentProject.project_id" :current-batch="currentBatch" :batches="recentBatches" :project-id="currentProject.project_id" :read-only="currentProject.status !== 'ACTIVE'" :can-operate="canOperateProject" :can-manage="canManageProject" :system-admin="systemAdmin" @analysis-completed="handleAnalysisCompleted" @disposition-completed="handleDispositionCompleted" @report-downloaded="reportDownloaded = true" @demo-reset="handleDemoReset" />
    </template>
    </template>
  </main>
</template>
