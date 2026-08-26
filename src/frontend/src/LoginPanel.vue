<script setup lang="ts">
import { ref } from "vue";

import { login, type CurrentUser } from "./auth";

const props = defineProps<{ message?: string }>();
const emit = defineEmits<{ authenticated: [user: CurrentUser] }>();

const username = ref("");
const password = ref("");
const busy = ref(false);
const errorMessage = ref("");

async function submit() {
  if (!username.value.trim() || !password.value) {
    errorMessage.value = "请输入账号和密码。";
    return;
  }
  busy.value = true;
  errorMessage.value = "";
  try {
    const user = await login(username.value.trim().toLowerCase(), password.value);
    password.value = "";
    emit("authenticated", user);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "登录失败，请重试。";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="login-panel" aria-labelledby="login-title">
    <p class="eyebrow">身份验证</p>
    <h2 id="login-title">登录报警管理系统</h2>
    <p class="identity-copy">业务数据按账号和项目授权访问；操作记录使用当前登录身份，不需要重复填写操作者。</p>
    <p v-if="props.message" class="request-error" role="alert">{{ props.message }}</p>
    <p v-if="errorMessage" class="request-error" role="alert" data-testid="login-error">{{ errorMessage }}</p>
    <form class="login-form" @submit.prevent="submit">
      <label>账号<input v-model="username" data-testid="login-username" autocomplete="username" :disabled="busy" /></label>
      <label>密码<input v-model="password" data-testid="login-password" type="password" autocomplete="current-password" :disabled="busy" /></label>
      <button type="submit" data-testid="login-submit" :disabled="busy">{{ busy ? "正在登录…" : "登录" }}</button>
    </form>
  </section>
</template>
