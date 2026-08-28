import { apiFetch, apiJson, requireOk } from "./api";

export const AUDIT_EVENT_TYPES = [
  "IMPORT_CREATED",
  "IMPORT_REJECTED",
  "IMPORT_CONFIRMED",
  "ANALYSIS_STARTED",
  "ANALYSIS_COMPLETED",
  "ANALYSIS_FAILED",
  "RESULT_OVERRIDDEN",
  "DISPOSITION_CHANGED",
  "REPORT_EXPORTED",
  "PROJECT_CREATED",
  "PROJECT_UPDATED",
  "PROJECT_ARCHIVED",
  "PROJECT_RESTORED",
  "PROJECT_DELETED",
  "MANUAL_ALARM_CREATED",
  "MANUAL_ALARM_UPDATED",
  "MANUAL_ALARM_INVALIDATED",
] as const;

export type AuditEventType = (typeof AUDIT_EVENT_TYPES)[number];

export interface AuditEvent {
  event_id: string;
  event_type: AuditEventType;
  occurred_at: string;
  operator: string;
  target_type: string;
  target_id: string;
  result: string;
  trace_id: string;
  details: Record<string, unknown>;
}

export interface AuditPage {
  page: number;
  size: number;
  total: number;
  items: AuditEvent[];
}

export interface ResetResult {
  completed_at: string;
  deleted_counts: Record<string, number>;
  business_state: "EMPTY";
}

export interface DownloadedReport {
  blob: Blob;
  filename: string;
}

export async function fetchAuditEvents(
  page: number,
  size: number,
  eventType: string,
  projectId?: string,
): Promise<AuditPage> {
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (eventType) query.set("event_type", eventType);
  if (projectId) query.set("project_id", projectId);
  return apiJson<AuditPage>(await apiFetch(`/api/v1/audit-events?${query}`, {
    headers: { Accept: "application/json" },
  }));
}

function reportFilename(response: Response, runId: string, format: "pdf" | "xlsx"): string {
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded.replace(/^"|"$/g, ""));
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  return plain || `alarm-analysis-${runId}.${format}`;
}

export async function exportReport(
  runId: string,
  format: "pdf" | "xlsx",
): Promise<DownloadedReport> {
  const response = await requireOk(await apiFetch(`/api/v1/analyses/${runId}/reports/${format}`, {
    method: "POST",
    headers: { Accept: "application/octet-stream" },
  }));
  const blob = await response.blob();
  if (blob.size === 0) throw new Error("服务返回了空报告文件");
  return { blob, filename: reportFilename(response, runId, format) };
}

export async function resetDemo(confirmation: string): Promise<ResetResult> {
  return apiJson<ResetResult>(await apiFetch("/api/v1/demo/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ confirmation }),
  }));
}
