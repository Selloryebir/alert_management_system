<script setup lang="ts">
import { computed, reactive, ref } from "vue";

import ReviewOperations from "./ReviewOperations.vue";
import {
  fetchAlarmDetail,
  fetchDashboard,
  latestAnalysis,
  listAlarms,
  startAnalysis,
  updateClassification,
  updateDisposition,
  type AlarmClass,
  type AlarmDetail,
  type AlarmFilters,
  type AlarmPage,
  type AnalysisRun,
  type Dashboard,
  type DispositionStatus,
  type CauseCategory,
  type NoiseType,
} from "./business";
import type { ImportBatch } from "./imports";

const props = defineProps<{
  currentBatch?: ImportBatch;
  batches: ImportBatch[];
}>();
const emit = defineEmits<{ demoReset: [] }>();

const PAGE_SIZE = 20;
const analysisBusy = ref(false);
const detailBusy = ref(false);
const businessError = ref("");
const businessMessage = ref("");
const analysis = ref<AnalysisRun>();
const dashboard = ref<Dashboard>();
const alarmPage = ref<AlarmPage>();
const selectedAlarm = ref<AlarmDetail>();
const dispositionOperator = ref("");
const dispositionNote = ref("");
const classificationNoise = ref<NoiseType>("NORMAL");
const classificationClass = ref<AlarmClass>("STANDARD");
const classificationCause = ref<CauseCategory>("UNKNOWN");
const classificationOperator = ref("");
const classificationReason = ref("");
const filters = reactive<AlarmFilters>({
  priority: "",
  area: "",
  unit: "",
  noise_type: "",
  cause_category: "",
  disposition_status: "",
});

const actionableBatches = computed(() => {
  const byId = new Map<string, ImportBatch>();
  const values = props.currentBatch ? [props.currentBatch, ...props.batches] : props.batches;
  for (const batch of values) {
    if (["IMPORTED", "ANALYZING", "COMPLETED", "FAILED"].includes(batch.status)) {
      byId.set(batch.batch_id, batch);
    }
  }
  return [...byId.values()];
});

const lastPage = computed(() => {
  if (!alarmPage.value || alarmPage.value.total === 0) return 0;
  return Math.ceil(alarmPage.value.total / alarmPage.value.size) - 1;
});

function actionLabel(batch: ImportBatch): string {
  const currentStatus = analysis.value?.batch_id === batch.batch_id
    ? analysis.value.status
    : batch.status;
  if (currentStatus === "COMPLETED") return "查看分析";
  if (currentStatus === "ANALYZING") return "刷新分析";
  if (currentStatus === "FAILED") return "重试分析";
  return "开始分析";
}

function actionTestId(batch: ImportBatch): string {
  return props.currentBatch?.batch_id === batch.batch_id
    ? "start-analysis"
    : `batch-analysis-${batch.batch_id}`;
}

function entries(values: Record<string, number> | undefined): [string, number][] {
  return Object.entries(values ?? {}).sort(([first], [second]) => first.localeCompare(second));
}

function barWidth(count: number): string {
  const total = dashboard.value?.total ?? 0;
  if (total <= 0 || count <= 0) return "0%";
  return `${Math.max(3, Math.round((count / total) * 100))}%`;
}

function clearBusinessState() {
  businessError.value = "";
  businessMessage.value = "";
}

async function openCompletedAnalysis(run: AnalysisRun) {
  analysis.value = run;
  selectedAlarm.value = undefined;
  const [loadedDashboard, loadedAlarms] = await Promise.all([
    fetchDashboard(run.run_id),
    listAlarms(run.run_id, 0, PAGE_SIZE, filters),
  ]);
  dashboard.value = loadedDashboard;
  alarmPage.value = loadedAlarms;
  businessMessage.value = `分析运行 ${run.run_id} 已加载。`;
}

async function handleBatchAction(batch: ImportBatch) {
  analysisBusy.value = true;
  clearBusinessState();
  try {
    const currentStatus = analysis.value?.batch_id === batch.batch_id
      ? analysis.value.status
      : batch.status;
    const run = currentStatus === "IMPORTED" || currentStatus === "FAILED"
      ? await startAnalysis(batch.batch_id)
      : await latestAnalysis(batch.batch_id);
    analysis.value = run;
    if (run.status === "COMPLETED") {
      await openCompletedAnalysis(run);
    } else if (run.status === "FAILED") {
      dashboard.value = undefined;
      alarmPage.value = undefined;
      selectedAlarm.value = undefined;
      businessError.value = `分析失败：${run.failure || "算法服务未返回具体原因"}。请确认算法服务恢复后重试。`;
    } else {
      businessMessage.value = "分析仍在进行，请稍后点击“刷新分析”。";
    }
  } catch (error) {
    businessError.value = `分析请求失败：${error instanceof Error ? error.message : "未知错误"}。请检查主系统和算法服务后重试。`;
  } finally {
    analysisBusy.value = false;
  }
}

async function refreshAlarms(page = 0) {
  if (!analysis.value) return;
  analysisBusy.value = true;
  clearBusinessState();
  try {
    alarmPage.value = await listAlarms(analysis.value.run_id, page, PAGE_SIZE, filters);
    selectedAlarm.value = undefined;
    if (alarmPage.value.total === 0) {
      businessMessage.value = "当前筛选条件没有报警记录，请调整筛选后重试。";
    }
  } catch (error) {
    businessError.value = `报警列表加载失败：${error instanceof Error ? error.message : "未知错误"}。请检查主系统后重试。`;
  } finally {
    analysisBusy.value = false;
  }
}

function resetFilters() {
  filters.priority = "";
  filters.area = "";
  filters.unit = "";
  filters.noise_type = "";
  filters.cause_category = "";
  filters.disposition_status = "";
  void refreshAlarms(0);
}

async function selectAlarm(recordId: string) {
  if (!analysis.value) return;
  detailBusy.value = true;
  clearBusinessState();
  try {
    selectedAlarm.value = await fetchAlarmDetail(analysis.value.run_id, recordId);
    dispositionOperator.value = selectedAlarm.value.disposition.operator ?? "";
    dispositionNote.value = "";
    classificationNoise.value = selectedAlarm.value.noise_type as NoiseType;
    classificationClass.value = selectedAlarm.value.alarm_class as AlarmClass;
    classificationCause.value = selectedAlarm.value.cause_category as CauseCategory;
    classificationOperator.value = selectedAlarm.value.classification_override?.operator ?? "";
    classificationReason.value = "";
  } catch (error) {
    businessError.value = `报警详情加载失败：${error instanceof Error ? error.message : "未知错误"}。请重试。`;
  } finally {
    detailBusy.value = false;
  }
}

async function saveClassification() {
  if (!analysis.value || !selectedAlarm.value) return;
  const operator = classificationOperator.value.trim();
  const reason = classificationReason.value.trim();
  if (!operator || !reason) {
    businessError.value = "请填写分类修订操作者和修订理由后再保存。";
    return;
  }
  detailBusy.value = true;
  clearBusinessState();
  try {
    selectedAlarm.value = await updateClassification(
      analysis.value.run_id,
      selectedAlarm.value.record_id,
      {
        noise_type: classificationNoise.value,
        alarm_class: classificationClass.value,
        cause_category: classificationCause.value,
      },
      operator,
      reason,
    );
    classificationNoise.value = selectedAlarm.value.noise_type as NoiseType;
    classificationClass.value = selectedAlarm.value.alarm_class as AlarmClass;
    classificationCause.value = selectedAlarm.value.cause_category as CauseCategory;
    classificationReason.value = "";
    const page = alarmPage.value?.page ?? 0;
    [dashboard.value, alarmPage.value] = await Promise.all([
      fetchDashboard(analysis.value.run_id),
      listAlarms(analysis.value.run_id, page, PAGE_SIZE, filters),
    ]);
    businessMessage.value = "人工分类修订已保存；算法原值保持不变。";
  } catch (error) {
    businessError.value = `分类修订失败：${error instanceof Error ? error.message : "未知错误"}。请核对当前有效值后重试。`;
  } finally {
    detailBusy.value = false;
  }
}

function handleDemoReset() {
  analysis.value = undefined;
  dashboard.value = undefined;
  alarmPage.value = undefined;
  selectedAlarm.value = undefined;
  businessError.value = "";
  businessMessage.value = "";
  dispositionOperator.value = "";
  dispositionNote.value = "";
  classificationOperator.value = "";
  classificationReason.value = "";
  filters.priority = "";
  filters.area = "";
  filters.unit = "";
  filters.noise_type = "";
  filters.cause_category = "";
  filters.disposition_status = "";
  emit("demoReset");
}

async function changeDisposition(status: DispositionStatus) {
  if (!analysis.value || !selectedAlarm.value) return;
  const operator = dispositionOperator.value.trim();
  const note = dispositionNote.value.trim();
  if (!operator) {
    businessError.value = "请填写操作者后再提交处置。";
    return;
  }
  if (!note) {
    businessError.value = "请填写处置说明后再提交处置。";
    return;
  }
  detailBusy.value = true;
  clearBusinessState();
  try {
    await updateDisposition(
      analysis.value.run_id,
      selectedAlarm.value.record_id,
      status,
      operator,
      note,
    );
    selectedAlarm.value = await fetchAlarmDetail(
      analysis.value.run_id,
      selectedAlarm.value.record_id,
    );
    dispositionNote.value = "";
    businessMessage.value = `处置状态已更新为 ${status}。`;
    const page = alarmPage.value?.page ?? 0;
    alarmPage.value = await listAlarms(analysis.value.run_id, page, PAGE_SIZE, filters);
  } catch (error) {
    businessError.value = `处置更新失败：${error instanceof Error ? error.message : "未知错误"}。请核对当前状态后重试。`;
  } finally {
    detailBusy.value = false;
  }
}
</script>

<template>
  <section class="business-panel" aria-labelledby="business-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">M3–M4 · 分析与审核</p>
        <h2 id="business-title">报警业务闭环</h2>
      </div>
    </div>

    <p v-if="actionableBatches.length === 0" class="empty-copy" data-testid="empty-state">
      尚无可分析批次。请先在导入向导中确认一个合成数据文件。
    </p>

    <div v-else class="batch-actions" aria-label="可分析批次">
      <article v-for="batch in actionableBatches" :key="batch.batch_id" class="batch-action-card">
        <div>
          <strong>{{ batch.file_name }}</strong>
          <span>{{ batch.status }} · {{ batch.total_rows }} 行</span>
        </div>
        <button
          type="button"
          :data-testid="actionTestId(batch)"
          :disabled="analysisBusy"
          @click="handleBatchAction(batch)"
        >
          {{ actionLabel(batch) }}
        </button>
      </article>
    </div>

    <p v-if="businessError" class="request-error" role="alert" data-testid="service-error">
      {{ businessError }}
    </p>
    <p v-if="businessMessage" class="import-message" role="status">{{ businessMessage }}</p>

    <template v-if="analysis?.status === 'COMPLETED' && dashboard">
      <section class="dashboard" aria-labelledby="dashboard-title">
        <div class="panel-heading compact-heading">
          <div>
            <p class="eyebrow">固定事实源统计</p>
            <h3 id="dashboard-title">分析总览</h3>
          </div>
          <p>规则版本 {{ analysis.rule_version }}</p>
        </div>

        <div class="summary-cards">
          <article><span>报警总数</span><strong data-testid="dashboard-total">{{ dashboard.total }}</strong></article>
          <article><span>待处理</span><strong>{{ dashboard.disposition_counts.OPEN ?? 0 }}</strong></article>
          <article><span>处理中</span><strong>{{ dashboard.disposition_counts.IN_PROGRESS ?? 0 }}</strong></article>
          <article><span>已关闭</span><strong>{{ dashboard.disposition_counts.CLOSED ?? 0 }}</strong></article>
          <article><span>关联事件链</span><strong data-testid="dashboard-chains">{{ analysis.summary?.event_chain_count ?? 0 }}</strong></article>
        </div>

        <div class="dashboard-grid">
          <article class="metric-panel">
            <h4>小时趋势</h4>
            <div v-if="dashboard.trend.length" class="metric-list">
              <div v-for="point in dashboard.trend" :key="point.bucket" class="metric-row">
                <span>{{ point.bucket }}</span><span class="bar-track"><i :style="{ width: barWidth(point.count) }" /></span><strong>{{ point.count }}</strong>
              </div>
            </div>
            <p v-else class="empty-copy">暂无趋势数据。</p>
          </article>

          <article v-for="group in [
            { title: '优先级分布', values: dashboard.priority_counts },
            { title: '区域分布', values: dashboard.area_counts },
            { title: '单元分布', values: dashboard.unit_counts },
          ]" :key="group.title" class="metric-panel">
            <h4>{{ group.title }}</h4>
            <div v-if="entries(group.values).length" class="metric-list">
              <div v-for="[label, count] in entries(group.values)" :key="label" class="metric-row">
                <span>{{ label }}</span><span class="bar-track"><i :style="{ width: barWidth(count) }" /></span><strong>{{ count }}</strong>
              </div>
            </div>
            <p v-else class="empty-copy">暂无数据。</p>
          </article>

          <article class="metric-panel">
            <h4>噪声类型分布</h4>
            <div class="metric-list">
              <div
                v-for="[label, count] in entries(dashboard.noise_type_counts)"
                :key="label"
                class="metric-row"
                :data-testid="`dashboard-noise-${label}`"
              >
                <span>{{ label }}</span><span class="bar-track"><i :style="{ width: barWidth(count) }" /></span><strong>{{ count }}</strong>
              </div>
            </div>
          </article>

          <article class="metric-panel">
            <h4>原因建议分布</h4>
            <div class="metric-list">
              <div
                v-for="[label, count] in entries(dashboard.cause_category_counts)"
                :key="label"
                class="metric-row"
                :data-testid="`dashboard-cause-${label}`"
              >
                <span>{{ label }}</span><span class="bar-track"><i :style="{ width: barWidth(count) }" /></span><strong>{{ count }}</strong>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="alarm-browser" aria-labelledby="alarm-list-title">
        <h3 id="alarm-list-title">报警列表</h3>
        <form class="filter-grid" @submit.prevent="refreshAlarms(0)">
          <label>优先级<select v-model="filters.priority"><option value="">全部</option><option v-for="value in ['P1','P2','P3','P4']" :key="value">{{ value }}</option></select></label>
          <label>区域<input v-model="filters.area" placeholder="精确区域" /></label>
          <label>单元<input v-model="filters.unit" placeholder="精确单元" /></label>
          <label>噪声类型<select v-model="filters.noise_type" data-testid="filter-noise"><option value="">全部</option><option v-for="value in ['NORMAL','DUPLICATE','CHATTER','SHORT_LIVED','PERSISTENT']" :key="value">{{ value }}</option></select></label>
          <label>原因建议<select v-model="filters.cause_category" data-testid="filter-cause"><option value="">全部</option><option v-for="value in ['PROCESS_DISTURBANCE','EQUIPMENT_FAULT','INSTRUMENT_ISSUE','MAINTENANCE_TEST','UNKNOWN']" :key="value">{{ value }}</option></select></label>
          <label>处置状态<select v-model="filters.disposition_status"><option value="">全部</option><option v-for="value in ['OPEN','IN_PROGRESS','CLOSED']" :key="value">{{ value }}</option></select></label>
          <div class="filter-actions"><button type="submit" :disabled="analysisBusy">应用筛选</button><button type="button" class="secondary-button" :disabled="analysisBusy" @click="resetFilters">清空筛选</button></div>
        </form>

        <p v-if="alarmPage && alarmPage.items.length === 0" class="empty-copy" data-testid="empty-state">
          当前条件下没有报警记录，请清空或调整筛选。
        </p>
        <div v-else-if="alarmPage" class="table-wrap">
          <table>
            <caption>共 {{ alarmPage.total }} 条，第 {{ alarmPage.page + 1 }} 页</caption>
            <thead><tr><th>源行</th><th>时间</th><th>位置</th><th>位号/描述</th><th>优先级</th><th>分析</th><th>处置</th></tr></thead>
            <tbody>
              <tr v-for="item in alarmPage.items" :key="item.record_id">
                <td>{{ item.source_row }}</td><td>{{ item.event_time }}</td><td>{{ item.site }} / {{ item.area }} / {{ item.unit || '—' }}</td>
                <td><button type="button" class="table-link" :data-testid="`alarm-row-${item.source_row}`" @click="selectAlarm(item.record_id)">{{ item.tag }}<small>{{ item.description }}</small></button></td>
                <td>{{ item.priority }} / {{ item.alarm_state }}</td><td>{{ item.noise_type }}<small>{{ item.cause_category }} · {{ item.score }}</small></td><td>{{ item.disposition_status }}</td>
              </tr>
            </tbody>
          </table>
          <div class="pagination"><button type="button" class="secondary-button" :disabled="analysisBusy || alarmPage.page <= 0" @click="refreshAlarms(alarmPage.page - 1)">上一页</button><span>{{ alarmPage.page + 1 }} / {{ lastPage + 1 }}</span><button type="button" class="secondary-button" :disabled="analysisBusy || alarmPage.page >= lastPage" @click="refreshAlarms(alarmPage.page + 1)">下一页</button></div>
        </div>
      </section>

      <article v-if="selectedAlarm" class="alarm-detail" data-testid="alarm-detail">
        <div class="panel-heading compact-heading"><div><p class="eyebrow">规范化详情</p><h3>{{ selectedAlarm.tag }}</h3></div><span class="status-badge" :class="`disposition-${selectedAlarm.disposition.status.toLowerCase()}`">{{ selectedAlarm.disposition.status }}</span></div>
        <dl class="detail-grid">
          <div><dt>源行</dt><dd data-testid="detail-source-row">{{ selectedAlarm.source_row }}</dd></div><div><dt>发生时间</dt><dd>{{ selectedAlarm.event_time }}</dd></div>
          <div><dt>恢复时间</dt><dd>{{ selectedAlarm.return_time || '—' }}</dd></div><div><dt>确认时间</dt><dd>{{ selectedAlarm.ack_time || '—' }}</dd></div>
          <div><dt>位置</dt><dd>{{ selectedAlarm.site }} / {{ selectedAlarm.area }} / {{ selectedAlarm.unit || '—' }}</dd></div><div><dt>描述</dt><dd>{{ selectedAlarm.description }}</dd></div>
          <div><dt>优先级/状态</dt><dd>{{ selectedAlarm.priority }} / {{ selectedAlarm.alarm_state }}</dd></div><div><dt>分析标签</dt><dd>{{ selectedAlarm.noise_type }} / {{ selectedAlarm.alarm_class }}</dd></div>
          <div><dt>原因建议</dt><dd>{{ selectedAlarm.cause_category }}</dd></div><div><dt>规则分数</dt><dd>{{ selectedAlarm.score }}</dd></div>
          <div><dt>值/阈值</dt><dd>{{ selectedAlarm.value ?? '—' }} / {{ selectedAlarm.threshold ?? '—' }} {{ selectedAlarm.engineering_unit || '' }}</dd></div><div><dt>来源/源操作员</dt><dd>{{ selectedAlarm.source_system || '—' }} / {{ selectedAlarm.operator || '—' }}</dd></div>
        </dl>

        <div class="detail-columns">
          <section data-testid="raw-payload"><h4>原始行</h4><dl class="raw-grid"><div v-for="[key, value] in Object.entries(selectedAlarm.raw_payload)" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div></dl></section>
          <section data-testid="detail-evidence"><h4>规则证据</h4><ul data-testid="evidence-list"><li v-for="item in selectedAlarm.evidence" :key="item">{{ item }}</li></ul></section>
        </div>

        <section class="chain-section" data-testid="detail-event-chains">
          <h4>相关事件链</h4>
          <p class="association-warning">以下内容是关联建议，不代表已确认根因。</p>
          <p v-if="selectedAlarm.event_chains.length === 0" class="empty-copy">该报警未关联事件链。</p>
          <article v-for="chain in selectedAlarm.event_chains" :key="chain.chain_id" class="chain-card" data-testid="event-chain">
            <strong>{{ chain.association_rule }}</strong><p>{{ chain.start_time }} 至 {{ chain.end_time }}</p><p>{{ chain.explanation }}</p>
            <p>成员源行：{{ chain.members.map((member) => member.source_row).join(' → ') }}</p>
          </article>
        </section>

        <section class="classification-editor">
          <h4>人工分类修订</h4>
          <p class="association-warning">人工修订只影响当前有效结论、看板和报告；算法原始结论保持不变。</p>
          <div class="classification-comparison">
            <div data-testid="classification-original">
              <strong>算法原值</strong>
              <span>{{ selectedAlarm.algorithm_classification.noise_type }} / {{ selectedAlarm.algorithm_classification.alarm_class }} / {{ selectedAlarm.algorithm_classification.cause_category }}</span>
            </div>
            <div data-testid="classification-effective">
              <strong>当前有效值</strong>
              <span>{{ selectedAlarm.noise_type }} / {{ selectedAlarm.alarm_class }} / {{ selectedAlarm.cause_category }}</span>
            </div>
          </div>
          <p v-if="selectedAlarm.classification_override" class="empty-copy">
            最近修订：{{ selectedAlarm.classification_override.operator }} · {{ selectedAlarm.classification_override.updated_at }} · {{ selectedAlarm.classification_override.reason }}
          </p>
          <div class="classification-grid">
            <label>噪声类型<select v-model="classificationNoise" data-testid="classification-noise" :disabled="detailBusy"><option v-for="value in ['NORMAL','DUPLICATE','CHATTER','SHORT_LIVED','PERSISTENT']" :key="value" :value="value">{{ value }}</option></select></label>
            <label>报警分类<select v-model="classificationClass" data-testid="classification-alarm-class" :disabled="detailBusy"><option v-for="value in ['NUISANCE','ACTIONABLE','STANDARD']" :key="value" :value="value">{{ value }}</option></select></label>
            <label>原因建议<select v-model="classificationCause" data-testid="classification-cause" :disabled="detailBusy"><option v-for="value in ['PROCESS_DISTURBANCE','EQUIPMENT_FAULT','INSTRUMENT_ISSUE','MAINTENANCE_TEST','UNKNOWN']" :key="value" :value="value">{{ value }}</option></select></label>
            <label>操作者（必填）<input v-model="classificationOperator" data-testid="classification-operator" :disabled="detailBusy" /></label>
            <label class="classification-reason">修订理由（必填）<textarea v-model="classificationReason" rows="2" data-testid="classification-reason" :disabled="detailBusy" /></label>
          </div>
          <button type="button" data-testid="classification-save" :disabled="detailBusy" @click="saveClassification">保存分类修订</button>
        </section>

        <section class="disposition-editor">
          <h4>人工处置</h4>
          <div class="editor-grid">
            <label>操作者<input v-model="dispositionOperator" data-testid="disposition-operator" :disabled="detailBusy" /></label>
            <label>处置说明（必填）<textarea v-model="dispositionNote" rows="2" data-testid="disposition-note" :disabled="detailBusy" /></label>
          </div>
          <div class="disposition-actions">
            <button v-if="selectedAlarm.disposition.status === 'OPEN'" type="button" data-testid="disposition-start" :disabled="detailBusy" @click="changeDisposition('IN_PROGRESS')">标记处理中</button>
            <template v-if="selectedAlarm.disposition.status === 'IN_PROGRESS'">
              <button type="button" data-testid="disposition-close" :disabled="detailBusy" @click="changeDisposition('CLOSED')">关闭报警</button>
              <button type="button" class="secondary-button" data-testid="disposition-reopen" :disabled="detailBusy" @click="changeDisposition('OPEN')">退回待处理</button>
            </template>
          </div>
          <div class="table-wrap" data-testid="disposition-history">
            <table><caption>处置历史</caption><thead><tr><th>时间</th><th>状态流转</th><th>操作者</th><th>说明</th></tr></thead><tbody><tr v-for="(item, index) in selectedAlarm.disposition_history" :key="`${item.occurred_at}-${index}`"><td>{{ item.occurred_at }}</td><td>{{ item.from_status }} → {{ item.to_status }}</td><td>{{ item.operator }}</td><td>{{ item.note || '—' }}</td></tr><tr v-if="selectedAlarm.disposition_history.length === 0"><td colspan="4">暂无处置历史。</td></tr></tbody></table>
          </div>
        </section>
      </article>
      <p v-if="detailBusy" class="import-message" role="status">正在加载或更新报警详情…</p>
    </template>

    <ReviewOperations :run-id="analysis?.status === 'COMPLETED' ? analysis.run_id : undefined" @demo-reset="handleDemoReset" />
  </section>
</template>
