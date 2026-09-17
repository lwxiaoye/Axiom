export type OfficialDomainRule = {
  host: string;
  include_subdomains: boolean;
};

export type KnowledgeBinding = {
  knowledge_id: string;
  knowledge_name_snapshot?: string;
  category?: string;
  department?: string;
  priority?: number;
  enabled?: boolean;
};

export type CampusRelease = {
  id: string;
  status: string;
  version_no?: number | null;
  model_id: string;
  main_chat_skin_id?: string | null;
  official_domains: OfficialDomainRule[];
  policy_version?: string;
  change_note?: string;
  config_hash?: string;
  created_by?: string;
  published_by?: string;
  created_at?: string | null;
  published_at?: string | null;
  knowledge_bindings: KnowledgeBinding[];
};

export type CampusModelOption = {
  id: string;
  name: string;
  is_default?: boolean;
};

export type CampusConfig = {
  id: string;
  tenant_id: string;
  revision: number;
  enabled: boolean;
  current_release: CampusRelease | null;
  draft: CampusRelease | null;
  available_models?: CampusModelOption[];
};

export type ValidationResult = {
  ok: boolean;
  errors: string[];
  warnings: string[];
  revision?: number;
};
