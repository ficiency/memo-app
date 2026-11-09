// Shapes the backend answers with (backend/app/api and app/services/memo_data.py).

export type Me = { id: string; email: string; name: string; firm: { id: string; name: string } };
export type MockUser = { email: string; name: string; firm: string };

export type SpecVersion = {
  id: string;
  number: number;
  label: string | null;
  status: string;
  created_from: string | null;
  created_at: string;
  page: [number, number];
  stages: string[];
};
export type Spec = { id: string; name: string; versions: SpecVersion[] };

export type Revision = {
  id: string;
  number: number;
  as_of: string | null;
  schema: string;
  created_at: string;
  created_by: string | null;
  previous_id: string | null;
  values?: number;
};
export type Deal = { id: string; name: string; revisions: Revision[] };

export type RecordValue = {
  path: string;
  family: string | null;
  label: string | null;
  kind: "number" | "text" | "date";
  value_kind: string | null;
  value: number | string | null;
  unit: string | null;
  period: string | null;
  forecast: boolean;
  source: string | null;
  changed: boolean;
  before: number | string | null;
};
export type RecordRevisionFull = Revision & { deal: string; values: RecordValue[] };

export type MemoSummary = {
  id: string;
  title: string;
  status: string;
  deal: string;
  stage: string | null;
  spec: string;
  spec_version: number;
  record_revision: number;
  memo_date: string | null;
  created_at: string;
  created_by: string | null;
};

// --- the memo as the canvas paints it ---------------------------------------------------------------
export type ParagraphStyle = {
  font: string | null;
  size_pt: number | null;
  bold: boolean | null;
  italic: boolean | null;
  caps: boolean | null;
  color_ref: string | null;
  align: string | null;
  anchor: string | null;
  line_spacing: number | null;
  space_before_pt: number | null;
  space_after_pt: number | null;
  bullet_char: string | null;
  bullet_color_ref: string | null;
  indent_in: number | null;
};
export type SegmentKind = "text" | "chip" | "model_draft" | "person_slot" | "conditional" | "page_number";
export type Seg = { id?: string | null; kind: SegmentKind; text: string; concept: string | null; path: string | null };
export type Paragraph = { level: number; role: string | null; style: ParagraphStyle; segments: Seg[] };

export type Frame = { x_in: number; y_in: number; w_in: number; h_in: number; rotation_deg: number; z_index: number };
export type Cell = {
  row: number;
  col: number;
  row_span: number;
  col_span: number;
  kind: string;
  fill: string | null;
  paragraphs: Paragraph[];
};
export type TableData = { columns: (number | null)[]; rows: { kind: string; height_in: number | null }[]; cells: Cell[] };
export type ChartData = {
  type: string;
  series: {
    name: string | null;
    concept: string | null;
    path: string | null;
    color: string | null;
    points: { category: string; value: number | null; forecast: boolean }[];
  }[];
};

export type El = {
  id: string;
  slide_id: string | null;
  type: "text" | "table" | "chart" | "image" | "shape" | "line" | "group";
  shape_kind: string | null;
  name: string | null;
  role: string | null;
  fill_kind: string;
  firm_lock: "fixed" | "content" | "free";
  master: boolean;
  user_lock: string | null;
  description: string | null;
  placeholder: boolean;
  fill: string | null;
  line: string | null;
  line_width_pt: number | null;
  revision: number;
  deleted: boolean;
  frame: Frame;
  paragraphs: Paragraph[];
  table: TableData | null;
  chart: ChartData | null;
};

export type SlideData = {
  id: string;
  position: number;
  kind: string;
  revision: number;
  section_id: string | null;
  layout: string | null;
  background: string | null;
  shows_master: boolean;
  elements: El[];
};

export type RoleStyle = {
  font: string | null;
  size_pt: number | null;
  min_size_pt: number | null;
  bold: boolean | null;
  italic: boolean | null;
  caps: boolean | null;
  color_ref: string | null;
};
export type SpecView = {
  version: { id: string; number: number; spec_id: string };
  page: { w_in: number; h_in: number };
  palette: Record<string, string>;
  roles: Record<string, RoleStyle>;
  fonts: Record<string, string>;
  table_style: Record<string, string | number | boolean | null> | null;
  chart_style: { series_colors: string[]; labels_on: boolean | null; gridlines: boolean | null; focus_color: string | null };
  grid: Record<string, number | null> | null;
  sections: Record<string, { name: string; status: string }>;
};

export type Person = { email: string; editing: string | null };

export type MemoView = {
  memo: {
    id: string;
    title: string;
    status: string;
    sequence: number;
    deal: string;
    stage: string;
    memo_date: string | null;
    record_revision: { id: string; number: number };
  };
  spec: SpecView;
  master: El[];
  slides: SlideData[];
  people: Person[];
};

export type Conflict = {
  id: string;
  kind: string;
  detail: string | null;
  status: string;
  element_id: string | null;
  created_at: string;
  operation: { type: string; actor: string; by: string | null; payload: Record<string, unknown> } | null;
};

export type ImportResult = {
  changes: number;
  applied?: number;
  refused?: number;
  kept_aside?: number;
  results: { type: string; target: string; status: string; reason: string | null }[];
};

export type RevisionInfo = { id: string; number: number; as_of: string | null; created_at: string; created_by: string | null };
export type UpdateInfo = { current: RevisionInfo; available: RevisionInfo | null; binding_changes: string[] };
export type UpdateChange = {
  slide: number;
  element_id: string;
  element: string;
  where: string;
  kind: "figure" | "chart";
  concept: string | null;
  before: string;
  after: string;
  missing: boolean;
};
export type UpdateChanges = {
  revision: RevisionInfo;
  current: RevisionInfo;
  changes: UpdateChange[];
  warnings: { slide: number; element: string; message: string }[];
};
