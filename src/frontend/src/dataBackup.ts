import { apiFetch, apiJson } from "./api";

export interface DataBackupStatus {
  database_size_bytes: number;
  deployment_mode: string;
  backup_management: string;
  recovery_point_count: number;
  recovery_points: Array<{
    backup_file: string;
    created_at: string | null;
    size_bytes: number;
    origin_instance_id: string | null;
    status: string;
    message: string;
  }>;
  latest_success_at: string | null;
  total_backup_bytes: number;
  all_hashes_valid: boolean | null;
  operator_instructions: string[];
}

export async function fetchDataBackupStatus(): Promise<DataBackupStatus> {
  return apiJson<DataBackupStatus>(await apiFetch("/api/v1/admin/data-backup-status", {
    headers: { Accept: "application/json" },
  }));
}
