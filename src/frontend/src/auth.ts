import { apiFetch, apiJson, requireOk, setCsrfToken, type CsrfToken } from "./api";

export type GlobalRole = "SYSTEM_ADMIN" | "NONE";
export type AccountStatus = "ACTIVE" | "DISABLED";
export type ProjectRole = "SYSTEM_ADMIN" | "MANAGER" | "ANALYST";

export interface CurrentUser {
  user_id: string;
  username: string;
  display_name: string;
  global_role: GlobalRole;
  must_change_password: boolean;
}

export interface UserAccount extends CurrentUser {
  status: AccountStatus;
  locked_until?: string | null;
  created_at: string;
}

export interface ProjectMember {
  user_id: string;
  username: string;
  display_name: string;
  global_role: GlobalRole;
  status: AccountStatus;
  project_role: "MANAGER" | "ANALYST";
}

export async function initializeCsrf(): Promise<CsrfToken> {
  const value = await apiJson<CsrfToken>(await apiFetch("/api/v1/auth/csrf", {
    headers: { Accept: "application/json" },
  }));
  setCsrfToken(value);
  return value;
}

export async function currentUser(): Promise<CurrentUser> {
  return apiJson<CurrentUser>(await apiFetch("/api/v1/auth/me", {
    headers: { Accept: "application/json" },
  }));
}

export async function login(username: string, password: string): Promise<CurrentUser> {
  await requireOk(await apiFetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ username, password }),
  }));
  await initializeCsrf();
  return currentUser();
}

export async function logout(): Promise<void> {
  await requireOk(await apiFetch("/api/v1/auth/logout", {
    method: "POST",
    headers: { Accept: "application/json" },
  }));
  setCsrfToken(undefined);
}

export async function changePassword(
  username: string,
  currentPassword: string,
  newPassword: string,
): Promise<CurrentUser> {
  await requireOk(await apiFetch("/api/v1/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  }));
  await initializeCsrf();
  return login(username, newPassword);
}

export async function listUsers(): Promise<UserAccount[]> {
  return apiJson<UserAccount[]>(await apiFetch("/api/v1/admin/users", {
    headers: { Accept: "application/json" },
  }));
}

export async function createUser(input: {
  username: string;
  display_name: string;
  password: string;
  global_role: GlobalRole;
}): Promise<UserAccount> {
  return apiJson<UserAccount>(await apiFetch("/api/v1/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function updateUser(
  userId: string,
  input: { display_name?: string; status?: AccountStatus; global_role?: GlobalRole },
): Promise<UserAccount> {
  return apiJson<UserAccount>(await apiFetch(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function resetUserPassword(userId: string, newPassword: string): Promise<UserAccount> {
  return apiJson<UserAccount>(await apiFetch(`/api/v1/admin/users/${userId}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ new_password: newPassword }),
  }));
}

export async function listProjectMembers(projectId: string): Promise<ProjectMember[]> {
  return apiJson<ProjectMember[]>(await apiFetch(`/api/v1/projects/${projectId}/members`, {
    headers: { Accept: "application/json" },
  }));
}

export async function putProjectMember(
  projectId: string,
  userId: string,
  projectRole: "MANAGER" | "ANALYST",
): Promise<ProjectMember> {
  return apiJson<ProjectMember>(await apiFetch(`/api/v1/projects/${projectId}/members/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ project_role: projectRole }),
  }));
}

export async function deleteProjectMember(projectId: string, userId: string): Promise<void> {
  await requireOk(await apiFetch(`/api/v1/projects/${projectId}/members/${userId}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  }));
}
