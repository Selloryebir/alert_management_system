<script setup lang="ts">
import { ref } from "vue";

import { changePassword, logout, type CurrentUser } from "./auth";

const props = defineProps<{ user: CurrentUser }>();
const emit = defineEmits<{ changed: [user: CurrentUser]; loggedOut: [] }>();

const showPassword = ref(props.user.must_change_password);
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const busy = ref(false);
const message = ref("");
const errorMessage = ref("");

function roleLabel(): string {
  return props.user.global_role === "SYSTEM_ADMIN" ? "系统管理员" : "项目用户";
}

async function savePassword() {
  errorMessage.value = "";
  message.value = "";
  if (!currentPassword.value || !newPassword.value) {
    errorMessage.value = "请填写当前密码和新密码。";
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    errorMessage.value = "两次输入的新密码不一致。";
    return;
  }
  busy.value = true;
  try {
    const user = await changePassword(props.user.username, currentPassword.value, newPassword.value);
    currentPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
    showPassword.value = false;
    message.value = "密码已更新，其他旧会话已失效。";
    emit("changed", user);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "密码更新失败。";
  } finally {
    busy.value = false;
  }
}

async function handleLogout() {
  busy.value = true;
  errorMessage.value = "";
  try {
    await logout();
    emit("loggedOut");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "退出失败，请重试。";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="account-panel" :class="{ forced: user.must_change_password }" aria-label="当前账号">
    <div class="account-summary">
      <div><strong>{{ user.display_name }}</strong><span>{{ user.username }} · {{ roleLabel() }}</span></div>
      <div class="account-actions">
        <button v-if="!user.must_change_password" type="button" class="secondary-button" :disabled="busy" @click="showPassword = !showPassword">{{ showPassword ? "取消改密" : "修改密码" }}</button>
        <button type="button" class="secondary-button" data-testid="logout-button" :disabled="busy" @click="handleLogout">退出登录</button>
      </div>
    </div>
    <p v-if="user.must_change_password" class="archive-notice" role="alert">当前使用临时密码。修改密码后才能访问项目和业务数据。</p>
    <form v-if="showPassword" class="password-form" data-testid="password-form" @submit.prevent="savePassword">
      <label>当前密码<input v-model="currentPassword" type="password" autocomplete="current-password" required /></label>
      <label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" minlength="12" maxlength="64" required /></label>
      <label>再次输入新密码<input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="12" maxlength="64" required /></label>
      <button type="submit" :disabled="busy">{{ busy ? "正在保存…" : "保存新密码" }}</button>
    </form>
    <p v-if="message" class="import-message" role="status">{{ message }}</p>
    <p v-if="errorMessage" class="request-error" role="alert">{{ errorMessage }}</p>
  </section>
</template>
