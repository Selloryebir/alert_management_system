import { apiFetch, apiJson } from "./api";

export type HealthStatus = "UP" | "DOWN" | "UNKNOWN";

export interface ComponentHealth {
  status: HealthStatus;
  detail?: string;
}
export interface HealthView {
  system: ComponentHealth;
  database: ComponentHealth;
  algorithm: ComponentHealth;
}

const unknownHealth = (): ComponentHealth => ({ status: "UNKNOWN" });

export const createUnknownHealth = (): HealthView => ({
  system: unknownHealth(),
  database: unknownHealth(),
  algorithm: unknownHealth(),
});

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : undefined;
}

function parseComponent(value: unknown): ComponentHealth {
  const component = asRecord(value);
  if (!component) return unknownHealth();

  const status =
    component.status === "UP" || component.status === "DOWN"
      ? component.status
      : "UNKNOWN";
  const detail =
    typeof component.detail === "string" && component.detail.trim()
      ? component.detail.trim()
      : undefined;

  return detail ? { status, detail } : { status };
}

export function parseHealthResponse(value: unknown): HealthView {
  const payload = asRecord(value);
  const components = asRecord(payload?.components);

  return {
    system: parseComponent(components?.system),
    database: parseComponent(components?.database),
    algorithm: parseComponent(components?.algorithm),
  };
}

export async function fetchHealth(): Promise<HealthView> {
  const response = await apiFetch("/api/v1/health", {
    headers: { Accept: "application/json" },
  });
  return parseHealthResponse(await apiJson<unknown>(response));
}
