// Shared domain types mirroring the FastAPI backend response models.
// Keeping these in one place makes the API contract explicit and the UI
// straightforward to extend as the backend evolves.

export type Lang = "en" | "zh";

export interface MatchedPath {
  input_hpo_id: string;
  input_hpo_name: string;
  matched_hpo_id: string;
  matched_hpo_name: string;
  match_type: "direct" | "child" | "parent" | "tpr" | string;
  frequency_weight: number;
  score_multiplier: number;
  contribution: number;
  explanation: string;
}

export interface MissingHpo {
  hpo_id: string;
  name: string;
}

export interface DiseaseResult {
  disease_id: string;
  disease_name: string;
  total_score: number;
  explained_ratio: number;
  matched_paths: MatchedPath[];
  missing_critical_hpos: MissingHpo[];
}

export interface SuggestedHpo {
  hpo_id: string;
  name: string;
  reason: string;
}

export interface AutoSelection {
  raw_description: string;
  matched_hpo_id?: string;
  matched_hpo_name?: string;
  match_method?: string;
}

export interface PredictResponse {
  query_hpo_ids: string[];
  results: DiseaseResult[];
  suggested_hpos: SuggestedHpo[];
  auto_selections?: AutoSelection[];
  computation_log?: unknown;
}

export interface SmartSearchResult {
  hpo_id: string;
  name: string;
  score: number;
  hint?: string;
}

export interface SmartSearchGroup {
  fragment: string;
  results: SmartSearchResult[];
}

export interface SmartSearchResponse {
  query: string;
  fragments: string[];
  groups: SmartSearchGroup[];
}

export interface DescribeMissingPrediction {
  disease_id: string;
  disease_name: string;
  matched_hpo_names: string[];
  missing_hpo_names: string[];
  explained_ratio: number;
}

export interface DescriptionItem {
  disease_id: string;
  disease_name: string;
  description: string;
}

export interface DescribeMissingResponse {
  descriptions: DescriptionItem[];
}

export type GuideExpertType = "doctor" | "team" | "department" | string;

export interface GuideHospital {
  id: string;
  name: string;
  city: string;
  map_query?: string;
  lat?: number | null;
  lng?: number | null;
  logo_url?: string | null;
  advantage?: string;
}

export interface GuideExpert {
  id: string;
  name: string;
  type: GuideExpertType;
  bio: string;
  hospital: GuideHospital;
}

export interface DiseaseGuideResponse {
  disease_id: string;
  available: boolean;
  name: string;
  name_alt: string;
  summary: string;
  care_tips: string[];
  specialty_keywords: string[];
  experts: GuideExpert[];
  cities: string[];
  message?: string | null;
}
