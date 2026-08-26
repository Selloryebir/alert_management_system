<script setup lang="ts">
import { reactive, ref, watch } from "vue";

import { priorityLabel, zh } from "./labels";
import {
  createManualAlarm,
  invalidateManualAlarm,
  listManualAlarms,
  updateManualAlarm,
  type ManualAlarm,
  type ManualAlarmInput,
} from "./projects";

const props = defineProps<{ projectId: string; site?: string; area?: string; readOnly?: boolean }>();
const emit = defineEmits<{ changed: [] }>();

const showCreate = ref(false);
const busy = ref(false);
const message = ref("");
const errorMessage = ref("");
const alarms = ref<ManualAlarm[]>([]);
const editing = ref<ManualAlarm>();
const invalidating = ref<ManualAlarm>();
const editedBy = ref("");
const editReason = ref("");
const invalidateOperator = ref("");
const invalidateReason = ref("");

function emptyForm(): ManualAlarmInput {
  return {
    event_time: "",
    return_time: null,
    ack_time: null,
    site: props.site ?? "",
    area: props.area ?? "",
    unit: null,
    tag: "",
    description: "",
    priority: "P2",
    state: "ACTIVE",
    value: null,
    threshold: null,
    engineering_unit: null,
    source_system: "MANUAL_ENTRY",
    operator: "",
  };
}
const form = reactive<ManualAlarmInput>(emptyForm());

async function loadAlarms() {
  busy.value = true;
  errorMessage.value = "";
  try {
    alarms.value = await listManualAlarms(props.projectId);
  } catch (error) {
    alarms.value = [];
    errorMessage.value = `人工补录加载失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

watch(() => props.projectId, async () => {
  alarms.value = [];
  editing.value = undefined;
  invalidating.value = undefined;
  Object.assign(form, emptyForm());
  await loadAlarms();
}, { immediate: true });

function toOffset(value: string | null | undefined): string | null {
  if (!value) return null;
  return new Date(value).toISOString();
}

function payload(): ManualAlarmInput {
  return {
    ...form,
    event_time: toOffset(form.event_time) ?? "",
    return_time: toOffset(form.return_time),
    ack_time: toOffset(form.ack_time),
    value: form.value === null || form.value === undefined || String(form.value) === "" ? null : Number(form.value),
    threshold: form.threshold === null || form.threshold === undefined || String(form.threshold) === "" ? null : Number(form.threshold),
  };
}

function editValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

async function saveCreate() {
  busy.value = true;
  message.value = "";
  errorMessage.value = "";
  try {
    await createManualAlarm(props.projectId, payload());
    showCreate.value = false;
    Object.assign(form, emptyForm());
    await loadAlarms();
    message.value = "人工报警补录成功，可在发起分析前继续修订或作废。";
    emit("changed");
  } catch (error) {
    errorMessage.value = `补录失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

function beginEdit(alarm: ManualAlarm) {
  editing.value = alarm;
  invalidating.value = undefined;
  Object.assign(form, {
    event_time: editValue(alarm.event_time),
    return_time: editValue(alarm.return_time),
    ack_time: editValue(alarm.ack_time),
    site: alarm.site,
    area: alarm.area,
    unit: alarm.unit ?? null,
    tag: alarm.tag,
    description: alarm.description,
    priority: alarm.priority,
    state: alarm.state,
    value: alarm.value ?? null,
    threshold: alarm.threshold ?? null,
    engineering_unit: alarm.engineering_unit ?? null,
    source_system: alarm.source_system,
    operator: alarm.operator ?? "",
  });
  editedBy.value = "";
  editReason.value = "";
}

async function saveEdit() {
  if (!editing.value) return;
  if (!editedBy.value.trim() || !editReason.value.trim()) {
    errorMessage.value = "请填写修订操作者和修订理由。";
    return;
  }
  busy.value = true;
  errorMessage.value = "";
  try {
    await updateManualAlarm(props.projectId, editing.value.record_id, {
      ...payload(), edited_by: editedBy.value.trim(), reason: editReason.value.trim(),
    });
    editing.value = undefined;
    Object.assign(form, emptyForm());
    await loadAlarms();
    message.value = "人工补录修订已保存。";
    emit("changed");
  } catch (error) {
    errorMessage.value = `修订失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

async function confirmInvalidation() {
  if (!invalidating.value) return;
  if (!invalidateOperator.value.trim() || !invalidateReason.value.trim()) {
    errorMessage.value = "请填写作废操作者和作废理由。";
    return;
  }
  busy.value = true;
  errorMessage.value = "";
  try {
    await invalidateManualAlarm(props.projectId, invalidating.value.record_id, invalidateOperator.value.trim(), invalidateReason.value.trim());
    invalidating.value = undefined;
    await loadAlarms();
    message.value = "人工补录报警已作废，后续分析会自动排除。";
    emit("changed");
  } catch (error) {
    errorMessage.value = `作废失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}
</script>

<template>
  <section class="manual-panel" data-testid="manual-alarm" aria-labelledby="manual-title">
    <div class="panel-heading compact-heading"><div><p class="eyebrow">单条业务录入</p><h3 id="manual-title">人工补录报警</h3></div><button type="button" class="secondary-button" :disabled="readOnly" @click="showCreate = !showCreate; editing = undefined">{{ showCreate ? "取消补录" : "人工补录" }}</button></div>
    <p class="empty-copy">适用于现场补录遗漏记录；补录后形成可追溯的独立批次，不代替文件批量导入。</p>
    <p v-if="message" class="import-message" role="status">{{ message }}</p><p v-if="errorMessage" class="request-error" role="alert">{{ errorMessage }}</p>

    <form v-if="showCreate || editing" class="manual-form" @submit.prevent="editing ? saveEdit() : saveCreate()">
      <label>发生时间（必填）<input v-model="form.event_time" type="datetime-local" required /></label>
      <label>厂区（必填）<input v-model="form.site" required /></label><label>区域（必填）<input v-model="form.area" required /></label><label>单元<input v-model="form.unit" /></label>
      <label>位号（必填）<input v-model="form.tag" required /></label><label class="wide-field">报警描述（必填）<input v-model="form.description" required /></label>
      <label>优先级<select v-model="form.priority"><option v-for="value in ['P1','P2','P3','P4']" :key="value" :value="value">{{ priorityLabel(value) }}</option></select></label>
      <label>报警状态<select v-model="form.state"><option v-for="value in ['ACTIVE','RETURNED','ACKNOWLEDGED']" :key="value" :value="value">{{ zh(value) }}</option></select></label>
      <label>来源系统（必填）<input v-model="form.source_system" required /></label><label>补录操作者（必填）<input v-model="form.operator" required /></label>
      <label>当时值<input v-model="form.value" type="number" step="any" /></label><label>阈值<input v-model="form.threshold" type="number" step="any" /></label>
      <template v-if="editing"><label>修订操作者（必填）<input v-model="editedBy" required /></label><label class="wide-field">修订理由（必填）<input v-model="editReason" required /></label></template>
      <button type="submit" :disabled="busy">{{ editing ? "保存修订" : "保存补录" }}</button>
    </form>

    <div v-if="alarms.length" class="manual-list"><article v-for="alarm in alarms" :key="alarm.record_id"><div><strong>{{ alarm.tag }}</strong><span>{{ alarm.description }}</span><small>{{ priorityLabel(alarm.priority) }} · {{ zh(alarm.state) }} · {{ alarm.invalidated_at ? "已作废" : "有效" }}</small></div><div v-if="!alarm.invalidated_at"><button type="button" class="secondary-button" :disabled="readOnly" @click="beginEdit(alarm)">编辑该补录</button><button type="button" class="danger-button" :disabled="readOnly" @click="invalidating = alarm; editing = undefined; showCreate = false">作废</button></div></article></div>

    <form v-if="invalidating" class="invalidation-form" @submit.prevent="confirmInvalidation"><h4>确认作废 {{ invalidating.tag }}</h4><label>作废操作者（必填）<input v-model="invalidateOperator" required /></label><label>作废理由（必填）<input v-model="invalidateReason" required /></label><button type="submit" class="danger-button" :disabled="busy">确认作废</button></form>
  </section>
</template>
