export type EntryType =
  | "table"
  | "column"
  | "metric"
  | "join_path"
  | "value"
  | "rule"
  | "playbook"
  | "caveat"
  | "dimension"
  | "chart_rule";

export interface Entry {
  id: string;
  type: EntryType;
  name: string;
  body: Record<string, any>;
  enabled: boolean;
  embed_status: string;
  embed_error: string;
  content_hash: string;
  created_at: string;
  updated_at: string;
}

export interface SaveResult {
  entry: Entry;
  embedded: boolean;
  embed_status: string;
  embed_error: string;
}

// ---- Phase 10: KB live updates (audit history + version) ----
export interface HistoryRow {
  history_id: number;
  entry_id: string;
  action: string; // create | update | delete | restore
  old_body: Record<string, any> | null;
  new_body: Record<string, any> | null;
  changed_at: string;
}

export interface Status {
  embedder: { model_name: string; dim: number; device: string; loaded: boolean };
  index: { size: number; dim: number; by_type: Record<string, number> };
  entries: { by_type: Record<string, number>; by_status: Record<string, number> };
  dialect: string;
}

// GET /api/health blocks used by the StatusBar traffic lights (plan §25.3).
export interface Health {
  llm?: { reachable?: boolean; model?: string | null; latency_ms?: number | null };
  embedder?: { ok?: boolean; model?: string | null; device?: string | null };
  search?: { enabled?: boolean; reachable?: boolean | null; url?: string };
}

export type FieldKind = "text" | "textarea" | "list" | "json" | "bool";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  lockOnEdit?: boolean; // natural-key fields that form the id
  help?: string;
}

// Field layout per entry type. Natural-key fields are locked when editing so the
// id stays stable (rename = delete + create).
export const FIELD_SPECS: Record<EntryType, FieldSpec[]> = {
  table: [
    { key: "table", label: "Table name", kind: "text", lockOnEdit: true },
    { key: "meaning", label: "Meaning (VI)", kind: "textarea" },
    { key: "meaning_en", label: "Meaning (EN)", kind: "textarea" },
    { key: "use_when", label: "Use when", kind: "list" },
    { key: "dont_use_when", label: "Do not use alone when", kind: "list" },
    { key: "primary_key", label: "Primary key", kind: "text" },
    { key: "columns", label: "Columns", kind: "list" },
    { key: "allowed_joins", label: "Allowed joins", kind: "list" },
    { key: "aliases", label: "Aliases", kind: "list" },
    { key: "retrieval_text", label: "Retrieval text", kind: "textarea" },
    { key: "common_values", label: "Common values (JSON)", kind: "json" },
  ],
  column: [
    { key: "table", label: "Table", kind: "text", lockOnEdit: true },
    { key: "column", label: "Column", kind: "text", lockOnEdit: true },
    { key: "data_type", label: "Data type", kind: "text" },
    { key: "meaning", label: "Meaning", kind: "textarea" },
    { key: "aliases", label: "Aliases", kind: "list" },
    { key: "use_when", label: "Use when", kind: "list" },
  ],
  metric: [
    { key: "metric", label: "Metric name", kind: "text", lockOnEdit: true },
    { key: "aliases", label: "Aliases", kind: "list" },
    { key: "formula", label: "Formula (SQLite)", kind: "textarea" },
    { key: "required_tables", label: "Required tables", kind: "list" },
    { key: "required_joins", label: "Required joins", kind: "list" },
    { key: "use_when", label: "Use when", kind: "textarea" },
    { key: "notes", label: "Notes", kind: "textarea" },
    // Phase 11 analytic extensions (optional; consumed by the advisor).
    { key: "direction", label: "Direction", kind: "text", help: "higher_is_better | lower_is_better | neutral" },
    { key: "decomposition", label: "Decomposition", kind: "list" },
    { key: "default_comparisons", label: "Default comparisons", kind: "list" },
    { key: "default_dimensions", label: "Default dimensions", kind: "list" },
    { key: "interpretation_down", label: "Interpretation when down", kind: "textarea" },
    { key: "interpretation_up", label: "Interpretation when up", kind: "textarea" },
  ],
  join_path: [
    { key: "name", label: "Name", kind: "text", lockOnEdit: true },
    { key: "tables", label: "Tables", kind: "list" },
    { key: "joins", label: "Joins", kind: "list" },
    { key: "use_when", label: "Use when", kind: "textarea" },
  ],
  value: [
    { key: "table", label: "Table", kind: "text", lockOnEdit: true },
    { key: "column", label: "Column", kind: "text", lockOnEdit: true },
    { key: "value", label: "Value", kind: "text" },
    { key: "id_column", label: "ID column", kind: "text" },
    { key: "id_value", label: "ID value", kind: "text", lockOnEdit: true },
    { key: "aliases", label: "Aliases", kind: "list" },
    { key: "use_when", label: "Use when", kind: "textarea" },
  ],
  rule: [
    { key: "section", label: "Section", kind: "text", lockOnEdit: true },
    { key: "title", label: "Title", kind: "text", lockOnEdit: true },
    { key: "content", label: "Content", kind: "textarea" },
    { key: "items", label: "Items", kind: "list" },
  ],
  // ---- Phase 11 analytic types (generic forms; rich playbook editor is Phase 16) ----
  playbook: [
    { key: "playbook", label: "Playbook slug", kind: "text", lockOnEdit: true },
    { key: "kind", label: "Kind", kind: "text", help: "diagnostic | comparison | ranking | overview" },
    { key: "aliases", label: "Aliases", kind: "list" },
    { key: "use_when", label: "Use when", kind: "textarea" },
    { key: "main_metrics", label: "Main metrics", kind: "list" },
    { key: "required_comparison", label: "Required comparison", kind: "text", help: "previous_period | same_period_last_year | none" },
    { key: "diagnostic_steps", label: "Diagnostic steps (JSON)", kind: "json" },
    { key: "interpretation_rules", label: "Interpretation rules", kind: "list" },
    { key: "improvement_rules", label: "Improvement rules", kind: "list" },
    { key: "caveats", label: "Caveats", kind: "list" },
    { key: "notes", label: "Notes", kind: "textarea" },
  ],
  caveat: [
    { key: "title", label: "Title", kind: "text", lockOnEdit: true },
    { key: "content", label: "Content", kind: "textarea" },
    { key: "applies_to_metrics", label: "Applies to metrics", kind: "list" },
    { key: "applies_to_tables", label: "Applies to tables", kind: "list" },
    { key: "severity", label: "Severity", kind: "text", help: "info | warning" },
    { key: "aliases", label: "Aliases", kind: "list" },
  ],
  dimension: [
    { key: "dimension", label: "Dimension slug", kind: "text", lockOnEdit: true },
    { key: "aliases", label: "Aliases", kind: "list" },
    { key: "table", label: "Table", kind: "text" },
    { key: "column", label: "Label column", kind: "text" },
    { key: "id_column", label: "ID column", kind: "text" },
    { key: "join_requirement", label: "Join path", kind: "text" },
    { key: "drill_down_to", label: "Drill down to", kind: "list" },
    { key: "use_when", label: "Use when", kind: "textarea" },
  ],
  chart_rule: [
    { key: "shape", label: "Shape", kind: "text", lockOnEdit: true, help: "kpi_comparison | trend | top_n | composition | raw" },
    { key: "chart_type", label: "Chart type", kind: "text", help: "grouped_bar | line | horizontal_bar | stacked_bar | none" },
    { key: "max_categories", label: "Max categories", kind: "text" },
    { key: "min_rows", label: "Min rows", kind: "text" },
    { key: "notes", label: "Notes", kind: "textarea" },
  ],
};

export const ENTRY_TYPES: EntryType[] = [
  "table", "column", "metric", "join_path", "value", "rule",
  "playbook", "caveat", "dimension", "chart_rule",
];

// ---- Phase 3/4: query-time retrieval result (mirrors backend/retrieval/models.py) ----
export interface ResolvedColumn {
  table: string;
  column: string;
  data_type: string;
  meaning: string;
  is_key: boolean;
}

export interface ResolvedTable {
  table: string;
  meaning: string;
  meaning_en: string;
  primary_key: string;
  columns: ResolvedColumn[];
  reason: string;
}

export interface ResolvedMetric {
  metric: string;
  formula: string;
  aliases: string[];
  required_tables: string[];
  required_joins: string[];
  use_when: string;
  notes: string;
  score: number;
}

export interface ResolvedJoin {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  condition: string;
  source: string;
}

export interface MatchedValue {
  table: string;
  column: string;
  value: string;
  id_column: string;
  id_value: string;
  matched_alias: string;
  match_kind: string;
}

export interface GlobalRule {
  section: string;
  title: string;
  content: string;
  items: string[];
}

export interface ResolvedContext {
  dialect: string;
  retrieval_query: string;
  pinned_tables: string[];
  final_tables: string[];
  tables: ResolvedTable[];
  columns: ResolvedColumn[];
  metrics: ResolvedMetric[];
  joins: ResolvedJoin[];
  matched_values: MatchedValue[];
  rules: GlobalRule[];
  debug: Record<string, any>;
}

export interface RetrievalPlan {
  needs_retrieval: boolean;
  retrieval_query: string | null;
  pinned_tables: string[];
  intent_hint: string;
  intent_reason?: string;
}

export interface ChatPlanResponse {
  conversation_id: string;
  retrieval_plan: RetrievalPlan;
  memory_window: string;
  resolved_context: ResolvedContext | null;
  llm_skill_context: string | null;
}

export interface AnalysisPlanResponse {
  conversation_id: string;
  mode: string;
  review_seed?: Record<string, any> | null;
  analytic_context?: Record<string, any> | null;
  note: string;
}

// ---- Phase 7/8: the real conversational turn (mirrors backend/api/chat.py) ----
export interface ResultEntityOut {
  type: string;
  id_column: string;
  id_value: string;
  name_column: string;
  name_value: string;
}

export interface ChatResponse {
  conversation_id: string;
  turn_id: string;
  intent: string;
  needs_sql: boolean;
  answer: string;
  standalone_question: string | null;
  sql: string | null;
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
  truncated: boolean;
  result_summary: string;
  result_entities: ResultEntityOut[];
  tables_used: string[];
  metrics_used: string[];
  filters_used: string[];
  validation_errors: string[];
  validation_warnings: string[];
  used_previous_context: boolean;
  repaired: boolean;
  llm_model: string;
  // The exact input sent to the model (logged per turn), same as the Chat Plan tab shows.
  llm_skill_context: string;
  llm_system_prompt: string;
  llm_user_prompt: string;
  llm_raw_response: string;
  timings_ms: Record<string, number>;
  error: string | null;
  // ---- Phase 13/14 analytic turn (empty on a normal turn) ----
  mode?: string;
  review_id?: string;
  report_markdown?: string;
  evidence?: EvidenceItem[];
  charts?: ChartSpec[];
  sources?: Record<string, any>[];
  follow_up_suggestions?: string[];
  caveats?: string[];
  analytic_status?: string;
}

// ---- Phase 14 analytic evidence + charts (mirrors backend/analysis/models.py) ----
export interface EvidenceItem {
  evidence_id: string;
  review_id: string;
  task_id: string;
  kind: string;
  source_type: string;
  metric: string;
  title: string;
  purpose: string;
  sql: string;
  columns: string[];
  rows: Record<string, any>[];
  profile: Record<string, any>;
  web: Record<string, any> | null;
  chart_id: string;
  status: string;
  created_at: string;
}

export interface ChartSeries {
  name: string;
  value_field: string;
}

export interface ChartSpec {
  chart_id: string;
  type: string;
  title: string;
  x_field: string;
  series: ChartSeries[];
  data: Record<string, any>[];
  unit: string;
  evidence_id: string;
  notes: string;
}

export interface ReviewRecord {
  review_id: string;
  conversation_id: string;
  turn_id: string;
  mode: string;
  question: string;
  plan?: Record<string, any> | null;
  findings_summary: string;
  report_markdown: string;
  evidence: EvidenceItem[];
  charts: ChartSpec[];
  sources: Record<string, any>[];
  follow_up_suggestions: string[];
  caveats: string[];
  status: string;
  created_at: string;
}

// ---- Persistent chat sessions (mirrors backend/api/conversations.py) ----
export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turn_count: number;
}

export interface HistoryTurn {
  turn_id: string;
  user_question: string;
  intent: string;
  needs_sql: boolean;
  answer: string;
  standalone_question: string;
  sql: string;
  columns: string[];
  rows: Record<string, any>[];
  row_count: number;
  truncated: boolean;
  tables_used: string[];
  metrics_used: string[];
  filters_used: string[];
  result_summary: string;
  review_id: string;
  error: string;
  llm_model: string;
  llm_skill_context: string;
  llm_system_prompt: string;
  llm_user_prompt: string;
  llm_raw_response: string;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turns: HistoryTurn[];
}

// ---- Streaming chat events (mirrors the /api/chat/stream SSE payloads) ----
export interface StepEvent {
  type: "step";
  step: string;
  status: "start" | "done";
  intent?: string;
  needs_retrieval?: boolean;
  reason?: string;
  query?: string;
  tables?: string[];
  skipped?: boolean;
  ms?: number;
  model?: string;
  error?: string;
  ok?: boolean;
  repaired?: boolean;
  errors?: string[];
  warnings?: string[];
  row_count?: number;
  truncated?: boolean;
  // ---- analytic steps (mode/plan/task/profile/charts/save) ----
  mode?: string;
  task_index?: number;
  task_total?: number;
  task_count?: number;
  title?: string;
  task_status?: string;
  source?: string;
  dropped?: string[];
  evidence_count?: number;
  chart_count?: number;
  review_id?: string;
  review_status?: string;
}

export interface TokenEvent {
  type: "token";
  delta: string;
}

export interface EvidenceEvent {
  type: "evidence";
  evidence: EvidenceItem;
}

export interface ChartEvent {
  type: "chart";
  chart: ChartSpec;
}

export interface FinalEvent {
  type: "final";
  response: ChatResponse;
}

export type ChatStreamEvent =
  | StepEvent
  | TokenEvent
  | EvidenceEvent
  | ChartEvent
  | FinalEvent;
