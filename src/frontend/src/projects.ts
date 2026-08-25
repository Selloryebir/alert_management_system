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
  created_at: string;
  updated_at: string;
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

async function apiResponse<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `请求失败（HTTP ${response.status}）`;
  try {
    const payload = (await response.json()) as { message?: string; failure?: string };
    message = payload.message || payload.failure || message;
  } catch {
    // 非 JSON 错误保留 HTTP 状态。
  }
  throw new Error(message);
}

export async function listProjects(query = "", includeArchived = false): Promise<Project[]> {
  const search = new URLSearchParams({
    q: query.trim(),
    include_archived: String(includeArchived),
  });
  return apiResponse<Project[]>(await fetch(`/api/v1/projects?${search}`, {
    headers: { Accept: "application/json" },
  }));
}

export async function createProject(input: ProjectInput): Promise<Project> {
  return apiResponse<Project>(await fetch("/api/v1/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function updateProject(projectId: string, input: Partial<ProjectInput>): Promise<Project> {
  return apiResponse<Project>(await fetch(`/api/v1/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function setProjectArchived(projectId: string, archived: boolean): Promise<Project> {
  return apiResponse<Project>(await fetch(`/api/v1/projects/${projectId}/${archived ? "archive" : "restore"}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  }));
}

export async function deleteProject(projectId: string): Promise<void> {
  const response = await fetch(`/api/v1/projects/${projectId}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) await apiResponse<never>(response);
}

export async function fetchProjectOverview(projectId: string): Promise<ProjectOverview> {
  return apiResponse<ProjectOverview>(await fetch(`/api/v1/projects/${projectId}/overview`, {
    headers: { Accept: "application/json" },
  }));
}

export async function exportProject(projectId: string): Promise<Blob> {
  const response = await fetch(`/api/v1/projects/${projectId}/export`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) await apiResponse<never>(response);
  return response.blob();
}

export async function createManualAlarm(projectId: string, input: ManualAlarmInput): Promise<ManualAlarm> {
  return apiResponse<ManualAlarm>(await fetch(`/api/v1/projects/${projectId}/manual-alarms`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function listManualAlarms(projectId: string): Promise<ManualAlarm[]> {
  return apiResponse<ManualAlarm[]>(await fetch(`/api/v1/projects/${projectId}/manual-alarms`, {
    headers: { Accept: "application/json" },
  }));
}

export async function updateManualAlarm(
  projectId: string,
  recordId: string,
  input: Partial<ManualAlarmInput> & { edited_by: string; reason: string },
): Promise<ManualAlarm> {
  return apiResponse<ManualAlarm>(await fetch(`/api/v1/projects/${projectId}/manual-alarms/${recordId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function invalidateManualAlarm(
  projectId: string,
  recordId: string,
  operator: string,
  reason: string,
): Promise<ManualAlarm> {
  return apiResponse<ManualAlarm>(await fetch(`/api/v1/projects/${projectId}/manual-alarms/${recordId}/invalidate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ operator, reason }),
  }));
}
