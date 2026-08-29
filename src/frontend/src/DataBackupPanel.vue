<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import type { CurrentUser } from "./auth";
import { fetchDataBackupStatus, type DataBackupStatus } from "./dataBackup";

const props = defineProps<{ user: CurrentUser }>();

const status = ref<DataBackupStatus>();
const loading = ref(false);
const errorMessage = ref("");
const systemAdmin = computed(() => props.user.global_role === "SYSTEM_ADMIN");
const nativeBackupOperations = computed(() => (
  status.value?.backup_management === "WINDOWS_NATIVE_SCRIPTS"
));

const hashLabel = computed(() => {
  if (status.value?.all_hashes_valid === true) return "完整校验通过";
  if (status.value?.all_hashes_valid === false) return "完整校验失败，请检查恢复点";
  return nativeBackupOperations.value
    ? "待运行 backup-status.ps1 完整校验"
    : "待由部署管理员执行完整校验";
});

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "未知";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || amount >= 10 ? 0 : 1;
  return `${amount.toFixed(digits)} ${units[unit]}`;
}

function formatDate(value: string | null): string {
  if (!value) return "暂无成功备份";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function deploymentLabel(value: string): string {
  if (value === "LOCAL_NATIVE") return "Windows 本机原生部署";
  return value;
}

function managementLabel(value: string): string {
  if (value === "WINDOWS_NATIVE_SCRIPTS") return "Windows 原生备份脚本";
  if (value === "DEPLOYMENT_MANAGED") return "由部署环境管理";
  return value;
}

function recoveryStatusLabel(value: string): string {
  if (value === "METADATA_OK") return "元数据可用";
  if (value === "INVALID") return "恢复点无效";
  if (value === "UNAVAILABLE") return "备份目录不可用";
  return value;
}

async function loadStatus() {
  if (!systemAdmin.value || loading.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    status.value = await fetchDataBackupStatus();
  } catch (error) {
    errorMessage.value = `备份状态加载失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally {
    loading.value = false;
  }
}

onMounted(() => void loadStatus());
</script>

<template>
  <section v-if="systemAdmin" class="data-backup-panel" aria-label="数据与备份">
    <div class="panel-heading compact-heading">
      <div><p class="eyebrow">系统管理</p><h2>数据容量与恢复点</h2></div>
      <button type="button" class="secondary-button" :disabled="loading" @click="loadStatus">
        {{ loading ? "加载中…" : "刷新状态" }}
      </button>
    </div>

    <p v-if="errorMessage" class="request-error" role="alert">{{ errorMessage }}</p>
    <p v-else-if="loading && !status" class="import-message" role="status">正在读取数据库与备份状态…</p>

    <template v-if="status">
      <dl class="backup-summary" aria-label="数据与备份摘要">
        <div><dt>数据库容量</dt><dd>{{ formatBytes(status.database_size_bytes) }}</dd></div>
        <div><dt>恢复点数</dt><dd>{{ status.recovery_point_count }}</dd></div>
        <div><dt>备份总容量</dt><dd>{{ formatBytes(status.total_backup_bytes) }}</dd></div>
        <div><dt>最近成功</dt><dd>{{ formatDate(status.latest_success_at) }}</dd></div>
      </dl>
      <p class="backup-runtime">
        部署方式：<strong>{{ deploymentLabel(status.deployment_mode) }}</strong>
        · 备份管理：<strong>{{ managementLabel(status.backup_management) }}</strong>
      </p>
      <p
        class="backup-hash-status"
        :class="{
          'hash-ok': status.all_hashes_valid === true,
          'hash-failed': status.all_hashes_valid === false,
          'hash-pending': status.all_hashes_valid === null,
        }"
        role="status"
      >
        SHA-256 状态：{{ hashLabel }}
      </p>

      <section aria-labelledby="recovery-points-title">
        <h3 id="recovery-points-title">恢复点明细</h3>
        <div v-if="status.recovery_points.length" class="table-wrap">
          <table data-testid="recovery-point-table">
            <thead><tr><th>文件</th><th>创建时间</th><th>容量</th><th>状态与提示</th></tr></thead>
            <tbody>
              <tr v-for="point in status.recovery_points" :key="`${point.backup_file}-${point.created_at}`">
                <td>{{ point.backup_file }}</td>
                <td>{{ formatDate(point.created_at) }}</td>
                <td>{{ formatBytes(point.size_bytes) }}</td>
                <td><strong>{{ recoveryStatusLabel(point.status) }}</strong><br />{{ point.message }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty-copy">当前没有可列出的恢复点。</p>
      </section>

      <section v-if="status.operator_instructions.length" aria-labelledby="backup-instructions-title">
        <h3 id="backup-instructions-title">当前环境操作提示</h3>
        <ul class="backup-instructions"><li v-for="instruction in status.operator_instructions" :key="instruction">{{ instruction }}</li></ul>
      </section>
    </template>

    <section v-if="nativeBackupOperations" class="backup-operations" aria-labelledby="backup-operations-title">
      <h3 id="backup-operations-title">Windows 原生包操作入口</h3>
      <p>请在安装目录打开 PowerShell，按实际任务运行对应脚本；恢复前先在隔离目录完成校验。</p>
      <dl>
        <div><dt>查看状态与完整校验</dt><dd><code>scripts\backup-status.ps1</code></dd></div>
        <div><dt>立即创建备份</dt><dd><code>scripts\backup.ps1</code></dd></div>
        <div><dt>隔离恢复校验</dt><dd><code>scripts\restore-verify.ps1</code></dd></div>
        <div><dt>管理定时备份</dt><dd><code>scripts\backup-schedule.ps1</code></dd></div>
      </dl>
    </section>
    <p v-else-if="status" class="empty-copy">当前环境的备份与恢复由部署管理员按对应部署说明管理。</p>
  </section>
</template>
