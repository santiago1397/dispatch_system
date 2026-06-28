/**
 * Application settings types — runtime-overridable LLM config.
 */

export type SettingSource = "db" | "env";

export interface LLMApiKeyView {
  is_set: boolean;
  last4: string | null;
  source: SettingSource;
}

export interface LLMBaseUrlView {
  value: string;
  source: SettingSource;
}

export interface LLMConfigRead {
  llm_api_key: LLMApiKeyView;
  llm_base_url: LLMBaseUrlView;
}

export interface LLMConfigUpdate {
  llm_api_key?: string;
  llm_base_url?: string;
}
