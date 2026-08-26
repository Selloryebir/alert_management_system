import { apiFetch, apiJson } from "./api";

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
  return apiJson<ImportBatch>(
    await apiFetch("/api/v1/imports/preview", { method: "POST", body }),
  );
}

export async function confirmImport(batchId: string): Promise<ImportBatch> {
  return apiJson<ImportBatch>(
    await apiFetch(`/api/v1/imports/${batchId}/confirm`, { method: "POST" }),
  );
}

export async function listImports(projectId: string): Promise<ImportBatch[]> {
  const query = new URLSearchParams({ limit: "20", project_id: projectId });
  return apiJson<ImportBatch[]>(
    await apiFetch(`/api/v1/imports?${query}`, {
      headers: { Accept: "application/json" },
    }),
  );
}
