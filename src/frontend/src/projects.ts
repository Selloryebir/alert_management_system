import { apiFetch, apiJson, requireOk } from "./api";
import type { ProjectRole } from "./auth";

export type ProjectStatus = "ACTIVE" | "ARCHIVED";

export interface ValidationRules {
  required_fields: string[];
  value_min?: number | null;
  value_max?: number | null;
  threshold_min?: number | null;
  threshold_max?: number | null;
}

export interface Project {
  project_id: string;
  code: string;
  name: string;
  client_name: string;
  site: string;
  unit_name: string;
  status: ProjectStatus;
  report_title: string;
  report_fields: string[];
  validation_rules?: ValidationRules;
  project_role: ProjectRole;
  created_at: string;
  updated_at: string;
}

export function projectDisplayName(project: Pick<Project, "code" | "name">): string {
  return project.code === "DEFAULT-DEMO" ? "默认分析项目" : project.name;
}

export function projectDisplayCode(project: Pick<Project, "code">): string {
  return project.code === "DEFAULT-DEMO" ? "DEFAULT" : project.code;
}

export function projectDisplayContext(
  project: Pick<Project, "code" | "client_name" | "site" | "unit_name">,
): { client: string; site: string; unit: string } {
  if (project.code !== "DEFAULT-DEMO") {
    return { client: project.client_name, site: project.site, unit: project.unit_name };
  }
  return { client: "初始客户", site: "初始厂区", unit: "初始装置" };
}

export interface ProjectInput {
  code: string;
  name: string;
  client_name: string;
  site: string;
  unit_name: string;
  report_title?: string;
  report_fields?: string[];
  validation_rules?: ValidationRules;
}

export interface ProjectOverview {
  project_id: string;
  statistics: {
    batch_count: number;
    alarm_count: number;
    valid_alarm_count: number;
    invalid_alarm_count: number;
    pending_disposition_count: number;
  };
  recent_tasks: Array<{ type: string; id: string; status: string; occurred_at: string }>;
}

export interface ManualAlarmInput {
  event_time: string;
  return_time?: string | null;
  ack_time?: string | null;
  site: string;
  area: string;
  unit?: string | null;
  tag: string;
  description: string;
  priority: string;
  state: string;
  value?: number | null;
  threshold?: number | null;
  engineering_unit?: string | null;
  source_system: string;
  operator?: string | null;
}

export interface ManualAlarm extends ManualAlarmInput {
  project_id: string;
  batch_id: string;
  record_id: string;
  raw_payload: Record<string, unknown>;
  invalidated_at?: string | null;
  invalidated_by?: string | null;
  invalidation_reason?: string | null;
}

export async function listProjects(query = "", includeArchived = false): Promise<Project[]> {
  const search = new URLSearchParams({
    q: query.trim(),
    include_archived: String(includeArchived),
  });
  return apiJson<Project[]>(await apiFetch(`/api/v1/projects?${search}`, {
    headers: { Accept: "application/json" },
  }));
}

export async function createProject(input: ProjectInput): Promise<Project> {
  return apiJson<Project>(await apiFetch("/api/v1/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function updateProject(projectId: string, input: Partial<ProjectInput>): Promise<Project> {
  return apiJson<Project>(await apiFetch(`/api/v1/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function setProjectArchived(projectId: string, archived: boolean): Promise<Project> {
  return apiJson<Project>(await apiFetch(`/api/v1/projects/${projectId}/${archived ? "archive" : "restore"}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  }));
}

export async function deleteProject(projectId: string): Promise<void> {
  await requireOk(await apiFetch(`/api/v1/projects/${projectId}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  }));
}

export async function fetchProjectOverview(projectId: string): Promise<ProjectOverview> {
  return apiJson<ProjectOverview>(await apiFetch(`/api/v1/projects/${projectId}/overview`, {
    headers: { Accept: "application/json" },
  }));
}

export async function exportProject(projectId: string): Promise<Blob> {
  const response = await requireOk(await apiFetch(`/api/v1/projects/${projectId}/export`, {
    headers: { Accept: "application/json" },
  }));
  return response.blob();
}

export async function createManualAlarm(projectId: string, input: ManualAlarmInput): Promise<ManualAlarm> {
  return apiJson<ManualAlarm>(await apiFetch(`/api/v1/projects/${projectId}/manual-alarms`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function listManualAlarms(projectId: string): Promise<ManualAlarm[]> {
  return apiJson<ManualAlarm[]>(await apiFetch(`/api/v1/projects/${projectId}/manual-alarms`, {
    headers: { Accept: "application/json" },
  }));
}

export async function updateManualAlarm(
  projectId: string,
  recordId: string,
  input: Partial<ManualAlarmInput> & { reason: string },
): Promise<ManualAlarm> {
  return apiJson<ManualAlarm>(await apiFetch(`/api/v1/projects/${projectId}/manual-alarms/${recordId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function invalidateManualAlarm(
  projectId: string,
  recordId: string,
  reason: string,
): Promise<ManualAlarm> {
  return apiJson<ManualAlarm>(await apiFetch(`/api/v1/projects/${projectId}/manual-alarms/${recordId}/invalidate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ reason }),
  }));
}
