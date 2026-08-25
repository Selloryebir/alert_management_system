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

export interface ImportBatch {
  batch_id: string;
  file_name: string;
  format: string;
  status: ImportStatus;
  total_rows: number;
  valid_rows: number;
  error_count: number;
  errors: ImportErrorItem[];
  preview_rows: AlarmPreview[];
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
  mapping?: Record<string, string>,
): Promise<ImportBatch> {
  const body = new FormData();
  body.append("file", file);
  if (mapping && Object.keys(mapping).length > 0) {
    body.append("mapping", JSON.stringify(mapping));
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

export async function listImports(): Promise<ImportBatch[]> {
  return apiResponse<ImportBatch[]>(
    await fetch("/api/v1/imports?limit=20", {
      headers: { Accept: "application/json" },
    }),
  );
}
