<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import {
  createProject,
  deleteProject,
  exportProject,
  fetchProjectOverview,
  listProjects,
  setProjectArchived,
  updateProject,
  type Project,
  type ProjectInput,
  type ProjectOverview,
  type ValidationRules,
} from "./projects";
import { fieldLabel, projectStatusLabel, zh } from "./labels";

const props = defineProps<{ systemAdmin?: boolean }>();
const emit = defineEmits<{ selected: [project?: Project] }>();

const REPORT_FIELDS = [
  ["summary", "分析摘要"],
  ["priority", "优先级分布"],
  ["area", "区域分布"],
  ["unit", "装置分布"],
  ["noise", "噪声类型"],
  ["cause", "原因建议"],
  ["disposition", "处置统计"],
  ["chains", "关联事件链"],
] as const;
const REQUIRED_FIELDS = ["site", "area", "unit", "tag", "description", "source_system"];

const projects = ref<Project[]>([]);
const selectedProject = ref<Project>();
const overview = ref<ProjectOverview>();
const query = ref("");
const includeArchived = ref(false);
const busy = ref(false);
const message = ref("");
const errorMessage = ref("");
const showCreate = ref(false);
const showSettings = ref(false);
const deleteConfirmation = ref("");
const createForm = reactive<ProjectInput>({ code: "", name: "", client_name: "", site: "", unit_name: "" });
const settings = reactive({
  name: "",
  client_name: "",
  site: "",
  unit_name: "",
  report_title: "",
  report_fields: [] as string[],
  required_fields: [] as string[],
  value_min: "",
  value_max: "",
  threshold_min: "",
  threshold_max: "",
});

const isReadOnly = computed(() => selectedProject.value?.status === "ARCHIVED");
const canManage = computed(() => Boolean(props.systemAdmin || selectedProject.value?.project_role === "MANAGER"));

function clearFeedback() {
  message.value = "";
  errorMessage.value = "";
}

async function refreshProjects() {
  busy.value = true;
  clearFeedback();
  try {
    projects.value = await listProjects(query.value, includeArchived.value);
    if (selectedProject.value) {
      const current = projects.value.find((item) => item.project_id === selectedProject.value?.project_id);
      if (current) selectedProject.value = current;
    }
    if (!selectedProject.value && projects.value.length === 1) {
      await chooseProject(projects.value[0]);
    }
  } catch (error) {
    errorMessage.value = `项目加载失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally {
    busy.value = false;
  }
}

function loadSettings(project: Project) {
  settings.name = project.name;
  settings.client_name = project.client_name ?? "";
  settings.site = project.site ?? "";
  settings.unit_name = project.unit_name ?? "";
  settings.report_title = project.report_title ?? project.name;
  settings.report_fields = [...(project.report_fields ?? REPORT_FIELDS.map(([key]) => key))];
  settings.required_fields = [...(project.validation_rules?.required_fields ?? [])];
  settings.value_min = numberText(project.validation_rules?.value_min);
  settings.value_max = numberText(project.validation_rules?.value_max);
  settings.threshold_min = numberText(project.validation_rules?.threshold_min);
  settings.threshold_max = numberText(project.validation_rules?.threshold_max);
}

function numberText(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

function optionalNumber(value: string): number | null {
  return value.trim() === "" ? null : Number(value);
}

async function chooseProject(project: Project) {
  selectedProject.value = project;
  loadSettings(project);
  showSettings.value = false;
  overview.value = undefined;
  clearFeedback();
  deleteConfirmation.value = "";
  emit("selected", project);
  await refreshOverview();
}

async function refreshOverview() {
  if (!selectedProject.value) return;
  overview.value = undefined;
  try {
    overview.value = await fetchProjectOverview(selectedProject.value.project_id);
  } catch (error) {
    errorMessage.value = `项目统计加载失败：${error instanceof Error ? error.message : "未知错误"}。`;
  }
}

async function submitProject() {
  if (![createForm.code, createForm.name, createForm.client_name, createForm.site, createForm.unit_name].every((value) => value?.trim())) {
    errorMessage.value = "请填写项目编号、项目名称、客户名称、厂区和装置。";
    return;
  }
  busy.value = true;
  clearFeedback();
  try {
    const project = await createProject({
      ...createForm,
      code: createForm.code.trim(),
      name: createForm.name.trim(),
      report_title: createForm.name.trim(),
      report_fields: REPORT_FIELDS.map(([key]) => key),
    });
    Object.assign(createForm, { code: "", name: "", client_name: "", site: "", unit_name: "" });
    showCreate.value = false;
    includeArchived.value = false;
    await refreshProjects();
    await chooseProject(project);
    message.value = `项目“${project.name}”已创建并选中。`;
  } catch (error) {
    errorMessage.value = `项目创建失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally {
    busy.value = false;
  }
}

async function saveSettings() {
  if (!selectedProject.value) return;
  if (!settings.name.trim()) {
    errorMessage.value = "项目名称不能为空。";
    return;
  }
  if (settings.report_fields.length === 0) {
    errorMessage.value = "报告至少保留一个内容模块。";
    return;
  }
  const rules: ValidationRules = {
    required_fields: settings.required_fields,
    value_min: optionalNumber(settings.value_min),
    value_max: optionalNumber(settings.value_max),
    threshold_min: optionalNumber(settings.threshold_min),
    threshold_max: optionalNumber(settings.threshold_max),
  };
  busy.value = true;
  clearFeedback();
  try {
    const updated = await updateProject(selectedProject.value.project_id, {
      name: settings.name.trim(),
      client_name: settings.client_name.trim(),
      site: settings.site.trim(),
      unit_name: settings.unit_name.trim(),
      report_title: settings.report_title.trim(),
      report_fields: settings.report_fields,
      validation_rules: rules,
    });
    selectedProject.value = updated;
    loadSettings(updated);
    emit("selected", updated);
    await refreshProjects();
    message.value = "项目资料、校验规则和报告字段已保存。";
  } catch (error) {
    errorMessage.value = `项目设置保存失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally {
    busy.value = false;
  }
}

async function toggleArchive() {
  if (!selectedProject.value) return;
  const archived = selectedProject.value.status === "ACTIVE";
  busy.value = true;
  clearFeedback();
  try {
    const updated = await setProjectArchived(selectedProject.value.project_id, archived);
    overview.value = undefined;
    selectedProject.value = updated;
    loadSettings(updated);
    emit("selected", updated);
    await refreshOverview();
    includeArchived.value = archived;
    await refreshProjects();
    message.value = archived ? "项目归档成功，只能查看历史数据。" : "项目恢复成功，可以继续导入和分析。";
  } catch (error) {
    errorMessage.value = `${archived ? "归档" : "恢复"}失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally {
    busy.value = false;
  }
}

async function resetAfterDemoReset() {
  selectedProject.value = undefined;
  overview.value = undefined;
  deleteConfirmation.value = "";
  showSettings.value = false;
  query.value = "";
  includeArchived.value = false;
  emit("selected", undefined);
  await refreshProjects();
  if (!selectedProject.value) {
    const preferred = projects.value.find((project) => project.code === "DEFAULT-DEMO")
      ?? projects.value.find((project) => project.status === "ACTIVE");
    if (preferred) await chooseProject(preferred);
  }
}

defineExpose({ refreshOverview, resetAfterDemoReset });

async function removeEmptyProject() {
  if (!selectedProject.value || deleteConfirmation.value !== selectedProject.value.code) return;
  const deletedName = selectedProject.value.name;
  busy.value = true;
  clearFeedback();
  try {
    await deleteProject(selectedProject.value.project_id);
    selectedProject.value = undefined;
    overview.value = undefined;
    deleteConfirmation.value = "";
    emit("selected", undefined);
    await refreshProjects();
    message.value = `空项目“${deletedName}”已删除。`;
  } catch (error) {
    errorMessage.value = `项目删除失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

async function downloadManifest() {
  if (!selectedProject.value) return;
  try {
    const blob = await exportProject(selectedProject.value.project_id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selectedProject.value.code}-项目清单.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    message.value = "项目清单已下载；该文件不替代数据库备份。";
  } catch (error) {
    errorMessage.value = `项目清单导出失败：${error instanceof Error ? error.message : "未知错误"}。`;
  }
}

onMounted(refreshProjects);
</script>

<template>
  <section id="project-workspace" class="project-panel" aria-labelledby="project-title">
    <div class="panel-heading">
      <div><p class="eyebrow">业务项目</p><h2 id="project-title">选择当前工作项目</h2></div>
      <button v-if="systemAdmin" type="button" @click="showCreate = !showCreate">{{ showCreate ? "取消新建" : "新建项目" }}</button>
    </div>
    <p class="identity-copy">导入、分析、处置和报告均归入当前项目；切换项目后不会带出其他项目的数据。</p>

    <form v-if="showCreate" class="project-form" data-testid="project-entry" @submit.prevent="submitProject">
      <label>项目编号（必填）<input v-model="createForm.code" autocomplete="off" /></label>
      <label>项目名称（必填）<input v-model="createForm.name" autocomplete="off" /></label>
      <label>客户名称（必填）<input v-model="createForm.client_name" /></label>
      <label>厂区（必填）<input v-model="createForm.site" /></label>
      <label>装置（必填）<input v-model="createForm.unit_name" /></label>
      <button type="submit" :disabled="busy">创建并选中</button>
    </form>

    <form class="project-search" @submit.prevent="refreshProjects">
      <label>搜索项目<input v-model="query" placeholder="编号、名称、客户或厂区" /></label>
      <label class="inline-check"><input v-model="includeArchived" type="checkbox" /><span>包含已归档项目</span></label>
      <button type="submit" class="secondary-button" :disabled="busy">{{ busy ? "加载中…" : "搜索" }}</button>
    </form>

    <p v-if="errorMessage" class="request-error" role="alert">{{ errorMessage }}</p>
    <p v-if="message" class="import-message" role="status">{{ message }}</p>
    <p v-if="projects.length === 0 && !busy" class="empty-copy">暂无匹配项目。请创建第一个项目，或调整搜索条件。</p>
    <div v-else class="project-list" data-testid="project-select">
      <article v-for="project in projects" :key="project.project_id" class="project-card" :class="{ selected: selectedProject?.project_id === project.project_id }">
        <div><strong>{{ project.name }}</strong><span>{{ project.code }} · {{ project.client_name || "未填写客户" }}</span><small>{{ project.site || "未填写厂区" }} / {{ project.unit_name || "未填写装置" }}</small></div>
        <div><span class="status-badge">{{ projectStatusLabel(project.status) }}</span><button type="button" class="secondary-button" :data-testid="`select-project-${project.code}`" @click="chooseProject(project)">{{ selectedProject?.project_id === project.project_id ? "当前项目" : "选择" }}</button></div>
      </article>
    </div>

    <section v-if="selectedProject" class="project-current" aria-label="当前项目">
      <div class="panel-heading compact-heading">
        <div><p class="eyebrow">当前项目</p><h3>当前：{{ selectedProject.name }}</h3><p>{{ selectedProject.code }} · {{ projectStatusLabel(selectedProject.status) }}</p></div>
        <div class="project-actions">
          <button v-if="canManage" type="button" class="secondary-button" @click="showSettings = !showSettings">{{ showSettings ? "收起设置" : "项目设置" }}</button>
          <button type="button" class="secondary-button" @click="downloadManifest">导出项目清单</button>
          <button v-if="canManage" type="button" :class="{ 'danger-button': !isReadOnly }" :disabled="busy" @click="toggleArchive">{{ isReadOnly ? "恢复项目" : "归档项目" }}</button>
        </div>
      </div>
      <div v-if="overview" class="project-overview">
        <span>导入批次 <strong>{{ overview.statistics.batch_count }}</strong></span><span>报警 <strong>{{ overview.statistics.alarm_count }}</strong></span><span>有效 <strong>{{ overview.statistics.valid_alarm_count }}</strong></span><span>已作废 <strong>{{ overview.statistics.invalid_alarm_count }}</strong></span><span>待处理 <strong>{{ overview.statistics.pending_disposition_count }}</strong></span>
      </div>
      <div v-if="overview?.recent_tasks.length" class="table-wrap"><table><caption>最近任务</caption><thead><tr><th>类型</th><th>状态</th><th>时间</th></tr></thead><tbody><tr v-for="task in overview.recent_tasks" :key="task.id"><td>{{ task.type === 'IMPORT' ? '文件导入' : '报警分析' }}</td><td>{{ zh(task.status) }}</td><td>{{ task.occurred_at }}</td></tr></tbody></table></div>
      <p v-if="isReadOnly" class="archive-notice">该项目为归档状态，可查看历史，但不能新增导入或启动分析。</p>
      <form v-if="systemAdmin && isReadOnly && overview?.statistics.batch_count === 0" class="project-delete" data-testid="delete-project" @submit.prevent="removeEmptyProject">
        <h4>删除未使用的空项目</h4>
        <p>仅已归档且从未产生业务数据的项目可删除。请输入项目编号“{{ selectedProject.code }}”确认。</p>
        <label>输入项目编号以确认删除<input v-model="deleteConfirmation" autocomplete="off" /></label>
        <button type="submit" class="danger-button" :disabled="busy || deleteConfirmation !== selectedProject.code">删除项目</button>
      </form>

      <form v-if="canManage && showSettings" class="project-settings" data-testid="project-settings" @submit.prevent="saveSettings">
        <h4>基础资料与报告</h4>
        <div class="settings-grid">
          <label>项目名称<input v-model="settings.name" :disabled="isReadOnly" /></label><label>客户名称<input v-model="settings.client_name" :disabled="isReadOnly" /></label>
          <label>厂区<input v-model="settings.site" :disabled="isReadOnly" /></label><label>装置<input v-model="settings.unit_name" :disabled="isReadOnly" /></label>
          <label class="wide-field">报告抬头<input v-model="settings.report_title" :disabled="isReadOnly" /></label>
        </div>
        <fieldset><legend>报告内容</legend><label v-for="[key, label] in REPORT_FIELDS" :key="key" class="inline-check"><input v-model="settings.report_fields" type="checkbox" :value="key" :disabled="isReadOnly" /><span>{{ label }}</span></label></fieldset>
        <h4>导入校验规则</h4>
        <p class="empty-copy">除系统必填项外，可指定附加必填字段和数值范围；空白表示不限制。</p>
        <fieldset><legend>附加必填字段</legend><label v-for="field in REQUIRED_FIELDS" :key="field" class="inline-check"><input v-model="settings.required_fields" type="checkbox" :value="field" :disabled="isReadOnly" /><span>{{ fieldLabel(field) }}</span></label></fieldset>
        <div class="settings-grid"><label>当时值最小值<input v-model="settings.value_min" type="number" :disabled="isReadOnly" /></label><label>当时值最大值<input v-model="settings.value_max" type="number" :disabled="isReadOnly" /></label><label>阈值最小值<input v-model="settings.threshold_min" type="number" :disabled="isReadOnly" /></label><label>阈值最大值<input v-model="settings.threshold_max" type="number" :disabled="isReadOnly" /></label></div>
        <button v-if="!isReadOnly" type="submit" :disabled="busy">保存项目设置</button>
      </form>
    </section>
  </section>
</template>
