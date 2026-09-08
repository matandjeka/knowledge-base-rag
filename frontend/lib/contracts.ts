export type Source = {source_id: string; name: string; status: string; config: {source_type: string; options: Record<string, unknown>}};
export type Job = {id: string; source_id: string; status: string; stage: string; step: number; processed: number; total: number; error?: string; dispatched?: boolean};
export type Citation = {citation_id: string; source_id: string; source_title?: string; excerpt: string; locator: string; retriever: string; score: number; locator_details: {source_type: string; url?: string}};
export type Answer = {answer: string; citations: Citation[]; insufficient_evidence: boolean; routing_trace?: unknown; evidence: unknown[]; citation_segments?: {text: string; citation_ids: string[]}[]};
export type Preview = {columns: {name: string; inferred_type: string}[]; sample_rows: Record<string, unknown>[]; row_count: number};
