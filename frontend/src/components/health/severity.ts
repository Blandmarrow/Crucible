import type { Severity } from "../../api/health";

export const SEVERITY_LABEL: Record<Severity, string> = { error: "Error", warn: "Warning", info: "Note" };
export const SEVERITY_CLASS: Record<Severity, string> = { error: "bad", warn: "warn", info: "info" };
