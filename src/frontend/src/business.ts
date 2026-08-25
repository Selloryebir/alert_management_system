export type AnalysisStatus = "ANALYZING" | "COMPLETED" | "FAILED";
export type DispositionStatus = "OPEN" | "IN_PROGRESS" | "CLOSED";

export interface AnalysisSummary {
  input_count: number;
  success_count: number;
  failure_count: number;
  noise_type_counts: Record<string, number>;
  cause_category_counts: Record<string, number>;
  event_chain_count: number;
}

export interface AnalysisRun {
  run_id: string;
  batch_id: string;
  attempt: number;
  status: AnalysisStatus;
  failure?: string;
  contract_version: string;
  algorithm_version: string;
  rule_version?: string;
  parameters: Record<string, unknown>;
  summary?: AnalysisSummary;
  started_at: string;
  completed_at?: string;
}

export interface CountPoint {
  bucket: string;
  count: number;
}

export interface Dashboard {
  run_id: string;
  batch_id: string;
  total: number;
  disposition_counts: Record<DispositionStatus, number>;
  trend: CountPoint[];
  priority_counts: Record<string, number>;
  area_counts: Record<string, number>;
  unit_counts: Record<string, number>;
  noise_type_counts: Record<string, number>;
  cause_category_counts: Record<string, number>;
}

export interface AlarmListItem {
  record_id: string;
  source_row: number;
  event_time: string;
  site: string;
  area: string;
  unit?: string;
  tag: string;
  description: string;
  priority: string;
  alarm_state: string;
  noise_type: string;
  alarm_class: string;
  cause_category: string;
  score: number;
  disposition_status: DispositionStatus;
}

export interface AlarmPage {
  items: AlarmListItem[];
  total: number;
  page: number;
  size: number;
}

export interface Disposition {
  status: DispositionStatus;
  operator?: string;
  note?: string;
  updated_at?: string;
  closed_at?: string;
}

export interface DispositionHistory {
  from_status: DispositionStatus;
  to_status: DispositionStatus;
  operator: string;
  note?: string;
  occurred_at: string;
}

export interface EventChainMember {
  record_id: string;
  source_row: number;
  order: number;
}

export interface EventChain {
  chain_id: string;
  start_time: string;
  end_time: string;
  association_rule: string;
  explanation: string;
  members: EventChainMember[];
}

export type NoiseType = "NORMAL" | "DUPLICATE" | "CHATTER" | "SHORT_LIVED" | "PERSISTENT";
export type AlarmClass = "NUISANCE" | "ACTIONABLE" | "STANDARD";
export type CauseCategory =
  | "PROCESS_DISTURBANCE"
  | "EQUIPMENT_FAULT"
  | "INSTRUMENT_ISSUE"
  | "MAINTENANCE_TEST"
  | "UNKNOWN";

export interface Classification {
  noise_type: NoiseType;
  alarm_class: AlarmClass;
  cause_category: CauseCategory;
}

export interface ClassificationOverride {
  operator: string;
  reason: string;
  updated_at: string;
}

export interface AlarmDetail extends AlarmListItem {
  return_time?: string;
  ack_time?: string;
  value?: number;
  threshold?: number;
  engineering_unit?: string;
  source_system?: string;
  operator?: string;
  raw_payload: Record<string, string>;
  evidence: string[];
  disposition: Disposition;
  disposition_history: DispositionHistory[];
  event_chains: EventChain[];
  algorithm_classification: Classification;
  classification_override?: ClassificationOverride | null;
}

export interface AlarmFilters {
  priority: string;
  area: string;
  unit: string;
  noise_type: string;
  cause_category: string;
  disposition_status: string;
}

async function apiResponse<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `请求失败（HTTP ${response.status}）`;
  try {
    const payload = (await response.json()) as { message?: string; failure?: string };
    message = payload.message || payload.failure || message;
  } catch {
    // 非 JSON 错误保留 HTTP 状态，便于操作者定位服务问题。
  }
  throw new Error(message);
}

export async function startAnalysis(batchId: string): Promise<AnalysisRun> {
  return apiResponse<AnalysisRun>(
    await fetch(`/api/v1/imports/${batchId}/analyses`, { method: "POST" }),
  );
}

export async function latestAnalysis(batchId: string): Promise<AnalysisRun> {
  return apiResponse<AnalysisRun>(
    await fetch(`/api/v1/imports/${batchId}/analyses/latest`, {
      headers: { Accept: "application/json" },
    }),
  );
}

export async function fetchDashboard(runId: string): Promise<Dashboard> {
  return apiResponse<Dashboard>(
    await fetch(`/api/v1/analyses/${runId}/dashboard`, {
      headers: { Accept: "application/json" },
    }),
  );
}

export async function listAlarms(
  runId: string,
  page: number,
  size: number,
  filters: AlarmFilters,
): Promise<AlarmPage> {
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  for (const [key, value] of Object.entries(filters)) {
    if (value.trim()) query.set(key, value.trim());
  }
  return apiResponse<AlarmPage>(
    await fetch(`/api/v1/analyses/${runId}/alarms?${query}`, {
      headers: { Accept: "application/json" },
    }),
  );
}

export async function fetchAlarmDetail(runId: string, recordId: string): Promise<AlarmDetail> {
  return apiResponse<AlarmDetail>(
    await fetch(`/api/v1/analyses/${runId}/alarms/${recordId}`, {
      headers: { Accept: "application/json" },
    }),
  );
}

export async function updateDisposition(
  runId: string,
  recordId: string,
  status: DispositionStatus,
  operator: string,
  note: string,
): Promise<Disposition> {
  return apiResponse<Disposition>(
    await fetch(`/api/v1/analyses/${runId}/alarms/${recordId}/disposition`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ status, operator, note }),
    }),
  );
}

export async function updateClassification(
  runId: string,
  recordId: string,
  classification: Classification,
  operator: string,
  reason: string,
): Promise<AlarmDetail> {
  return apiResponse<AlarmDetail>(
    await fetch(`/api/v1/analyses/${runId}/alarms/${recordId}/classification`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ ...classification, operator, reason }),
    }),
  );
}
