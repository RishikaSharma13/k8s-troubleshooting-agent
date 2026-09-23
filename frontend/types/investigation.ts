export type Diagnosis = {
  available?: boolean;
  root_cause?: string;
  explanation?: string;
  suggested_fix?: string;
  kubectl_commands?: string[];
  prevention_recommendation?: string;
  confidence?: number;
  error?: string;
};

export type InvestigationResponse = {
  status: string;
  investigation: Record<string, unknown>;
  diagnosis: Diagnosis;
};

export type HistoryRow = {
  id: string;
  created_at: string;
  root_cause: string;
  namespace: string | null;
  confidence: number;
  status: string;
};

export type Cluster = { name: string; context: string; cluster?: string; server?: string; namespace?: string; current?: boolean };
export type ClusterResponse = { status: string; clusters: Cluster[]; error?: string };
