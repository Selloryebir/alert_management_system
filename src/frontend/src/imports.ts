export type ImportStatus =
  | "READY"
  | "REJECTED"
  | "IMPORTED"
  | "ANALYZING"
  | "COMPLETED"
  | "FAILED";

export interface ImportErrorItem {
  source_row: number;
  field: string;
  code: string;
  message: string;
}

export interface AlarmPreview {
  source_row: number;
  event_time?: string;
  tag?: string;
  description?: string;
  priority?: string;
  state?: string;
  raw_payload?: Record<string, string>;
}

export interface ImportSourceRow {
  source_row: number;
  values: Record<string, string>;
}

export type ImportCorrections = Record<string, Record<string, string>>;

export interface ImportBatch {
  batch_id: string;
  project_id?: string;
  file_name: string;
  format: string;
  status: ImportStatus;
  total_rows: number;
  valid_rows: number;
  error_count: number;
  errors: ImportErrorItem[];
  preview_rows: AlarmPreview[];
  headers?: string[];
  mapping?: Record<string, string>;
  corrections?: ImportCorrections;
  source_rows?: ImportSourceRow[];
  created_at: string;
  imported_at?: string;
}

async function apiResponse<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `请求失败（HTTP ${response.status}）`;
  try {
    const payload = (await response.json()) as { message?: string };
    if (payload.message) message = payload.message;
  } catch {
    // 非 JSON 错误仍保留可行动的 HTTP 状态。
  }
  throw new Error(message);
}

export async function previewImport(
  file: File,
  projectId: string,
  mapping?: Record<string, string>,
  corrections?: ImportCorrections,
): Promise<ImportBatch> {
  const body = new FormData();
  body.append("file", file);
  body.append("project_id", projectId);
  if (mapping && Object.keys(mapping).length > 0) {
    body.append("mapping", JSON.stringify(mapping));
  }
  if (corrections && Object.keys(corrections).length > 0) {
    body.append("corrections", JSON.stringify(corrections));
  }
  return apiResponse<ImportBatch>(
    await fetch("/api/v1/imports/preview", { method: "POST", body }),
  );
}

export async function confirmImport(batchId: string): Promise<ImportBatch> {
  return apiResponse<ImportBatch>(
    await fetch(`/api/v1/imports/${batchId}/confirm`, { method: "POST" }),
  );
}

export async function listImports(projectId: string): Promise<ImportBatch[]> {
  const query = new URLSearchParams({ limit: "20", project_id: projectId });
  return apiResponse<ImportBatch[]>(
    await fetch(`/api/v1/imports?${query}`, {
      headers: { Accept: "application/json" },
    }),
  );
}
