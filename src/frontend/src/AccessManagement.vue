<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";

import {
  createUser,
  deleteProjectMember,
  listProjectMembers,
  listUsers,
  putProjectMember,
  resetUserPassword,
  updateUser,
  type CurrentUser,
  type GlobalRole,
  type ProjectMember,
  type UserAccount,
} from "./auth";
import { projectDisplayName, type Project } from "./projects";

const props = defineProps<{ user: CurrentUser; project?: Project }>();

const users = ref<UserAccount[]>([]);
const members = ref<ProjectMember[]>([]);
const busy = ref(false);
const message = ref("");
const errorMessage = ref("");
const showCreate = ref(false);
const newUser = reactive({ username: "", display_name: "", password: "", global_role: "NONE" as GlobalRole });
const memberUserId = ref("");
const memberRole = ref<"MANAGER" | "ANALYST">("ANALYST");
const resetTarget = ref<UserAccount>();
const resetPassword = ref("");

const systemAdmin = computed(() => props.user.global_role === "SYSTEM_ADMIN");
const canManageMembers = computed(() => systemAdmin.value || props.project?.project_role === "MANAGER");

function clearFeedback() {
  message.value = "";
  errorMessage.value = "";
}

async function loadUsers() {
  if (!systemAdmin.value) return;
  busy.value = true;
  clearFeedback();
  try { users.value = await listUsers(); }
  catch (error) { errorMessage.value = `账号加载失败：${error instanceof Error ? error.message : "未知错误"}。`; }
  finally { busy.value = false; }
}

async function loadMembers() {
  if (!props.project || !canManageMembers.value) {
    members.value = [];
    return;
  }
  busy.value = true;
  clearFeedback();
  try { members.value = await listProjectMembers(props.project.project_id); }
  catch (error) { errorMessage.value = `项目成员加载失败：${error instanceof Error ? error.message : "未知错误"}。`; }
  finally { busy.value = false; }
}

async function submitUser() {
  if (!newUser.username.trim() || !newUser.display_name.trim() || !newUser.password) {
    errorMessage.value = "请填写账号、展示名和临时密码。";
    return;
  }
  busy.value = true;
  clearFeedback();
  try {
    await createUser({
      username: newUser.username.trim().toLowerCase(),
      display_name: newUser.display_name.trim(),
      password: newUser.password,
      global_role: newUser.global_role,
    });
    Object.assign(newUser, { username: "", display_name: "", password: "", global_role: "NONE" });
    showCreate.value = false;
    await loadUsers();
    message.value = "账号已创建，用户首次登录时必须修改临时密码。";
  } catch (error) {
    errorMessage.value = `账号创建失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

async function toggleUser(user: UserAccount) {
  busy.value = true;
  clearFeedback();
  try {
    await updateUser(user.user_id, { status: user.status === "ACTIVE" ? "DISABLED" : "ACTIVE" });
    await loadUsers();
    message.value = user.status === "ACTIVE" ? "账号已停用，已有会话将失效。" : "账号已启用。";
  } catch (error) {
    errorMessage.value = `账号状态更新失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

async function saveResetPassword() {
  if (!resetTarget.value || !resetPassword.value) return;
  busy.value = true;
  clearFeedback();
  try {
    await resetUserPassword(resetTarget.value.user_id, resetPassword.value);
    resetTarget.value = undefined;
    resetPassword.value = "";
    await loadUsers();
    message.value = "临时密码已重置，该账号需要重新登录并修改密码。";
  } catch (error) {
    errorMessage.value = `密码重置失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

async function saveMember(userId = memberUserId.value, role = memberRole.value) {
  if (!props.project || !userId.trim()) {
    errorMessage.value = "请选择账号或填写账号标识。";
    return;
  }
  busy.value = true;
  clearFeedback();
  try {
    await putProjectMember(props.project.project_id, userId.trim(), role);
    memberUserId.value = "";
    await loadMembers();
    message.value = "项目成员职责已保存。";
  } catch (error) {
    errorMessage.value = `成员保存失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

async function removeMember(member: ProjectMember) {
  if (!props.project) return;
  busy.value = true;
  clearFeedback();
  try {
    await deleteProjectMember(props.project.project_id, member.user_id);
    await loadMembers();
    message.value = "项目成员已移除。";
  } catch (error) {
    errorMessage.value = `成员移除失败：${error instanceof Error ? error.message : "未知错误"}。`;
  } finally { busy.value = false; }
}

watch(() => props.project?.project_id, loadMembers);
onMounted(() => {
  void loadUsers();
  void loadMembers();
});
</script>

<template>
  <section v-if="systemAdmin || (project && canManageMembers)" class="access-panel" aria-label="账号与项目权限">
    <p v-if="message" class="import-message" role="status">{{ message }}</p>
    <p v-if="errorMessage" class="request-error" role="alert">{{ errorMessage }}</p>

    <section v-if="systemAdmin" aria-labelledby="users-title">
      <div class="panel-heading compact-heading"><div><p class="eyebrow">系统管理</p><h3 id="users-title">账号管理</h3></div><button type="button" @click="showCreate = !showCreate">{{ showCreate ? "取消新建" : "新建账号" }}</button></div>
      <aside v-if="showCreate && newUser.global_role === 'SYSTEM_ADMIN'" class="admin-boundary-note">
        新建管理员账号前，请确认：分析结论来自规则证据与关联模型，应由授权人员结合现场工况形成正式处置结论；管理员可查看全部项目、审计记录和系统运维信息。
      </aside>
      <form v-if="showCreate" class="access-form" @submit.prevent="submitUser">
        <label>账号<input v-model="newUser.username" autocomplete="off" pattern="[a-z0-9._-]{3,50}" required /></label>
        <label>展示名<input v-model="newUser.display_name" maxlength="100" required /></label>
        <label>临时密码<input v-model="newUser.password" type="password" minlength="12" maxlength="64" autocomplete="new-password" required /></label>
        <label>全局职责<select v-model="newUser.global_role"><option value="NONE">项目用户</option><option value="SYSTEM_ADMIN">系统管理员</option></select></label>
        <button type="submit" :disabled="busy">创建账号</button>
      </form>
      <div v-if="users.length" class="table-wrap"><table data-testid="user-table"><thead><tr><th>账号</th><th>展示名</th><th>职责</th><th>状态</th><th>锁定至</th><th>操作</th></tr></thead><tbody><tr v-for="item in users" :key="item.user_id"><td>{{ item.username }}</td><td>{{ item.display_name }}</td><td>{{ item.global_role === 'SYSTEM_ADMIN' ? '系统管理员' : '项目用户' }}</td><td>{{ item.status === 'ACTIVE' ? '已启用' : '已停用' }}</td><td>{{ item.locked_until || '未锁定' }}</td><td><button type="button" class="secondary-button" :disabled="busy" @click="toggleUser(item)">{{ item.status === 'ACTIVE' ? '停用' : '启用' }}</button> <button type="button" class="secondary-button" :disabled="busy" @click="resetTarget = item; resetPassword = ''">重置密码</button></td></tr></tbody></table></div>
      <form v-if="resetTarget" class="access-form" @submit.prevent="saveResetPassword"><h4>重置 {{ resetTarget.display_name }} 的临时密码</h4><label>新临时密码<input v-model="resetPassword" type="password" minlength="12" maxlength="64" autocomplete="new-password" required /></label><button type="submit" :disabled="busy">确认重置</button><button type="button" class="secondary-button" @click="resetTarget = undefined">取消</button></form>
    </section>

    <section v-if="project && canManageMembers" aria-labelledby="members-title">
      <div class="panel-heading compact-heading"><div><p class="eyebrow">当前项目</p><h3 id="members-title">{{ projectDisplayName(project) }} · 成员职责</h3></div><button type="button" class="secondary-button" :disabled="busy" @click="loadMembers">刷新成员</button></div>
      <form v-if="systemAdmin" class="access-form" @submit.prevent="saveMember()">
        <label>账号
          <select v-model="memberUserId" required><option value="">请选择账号</option><option v-for="item in users.filter((value) => value.status === 'ACTIVE')" :key="item.user_id" :value="item.user_id">{{ item.display_name }}（{{ item.username }}）</option></select>
        </label>
        <label>项目职责<select v-model="memberRole"><option value="MANAGER">项目负责人</option><option value="ANALYST">分析人员</option></select></label>
        <button type="submit" :disabled="busy">添加或更新成员</button>
      </form>
      <p v-else class="empty-copy">项目负责人可调整或移除现有成员；新增账号并加入项目由系统管理员完成。</p>
      <div v-if="members.length" class="table-wrap"><table data-testid="member-table"><thead><tr><th>账号</th><th>展示名</th><th>项目职责</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="member in members" :key="member.user_id"><td>{{ member.username }}</td><td>{{ member.display_name }}</td><td>{{ member.project_role === 'MANAGER' ? '项目负责人' : '分析人员' }}</td><td>{{ member.status === 'ACTIVE' ? '已启用' : '已停用' }}</td><td><button type="button" class="secondary-button" :disabled="busy" @click="saveMember(member.user_id, member.project_role === 'MANAGER' ? 'ANALYST' : 'MANAGER')">改为{{ member.project_role === 'MANAGER' ? '分析人员' : '项目负责人' }}</button> <button type="button" class="danger-button" :disabled="busy" @click="removeMember(member)">移除</button></td></tr></tbody></table></div>
    </section>
  </section>
</template>
