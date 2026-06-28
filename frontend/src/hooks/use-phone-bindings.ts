"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";

export interface PhoneBinding {
  id: string;
  phone_e164: string;
  company_id: string;
  company_name: string;
  company_display_name: string | null;
  note: string | null;
  created_at: string;
}

interface PhoneBindingList {
  items: PhoneBinding[];
  total: number;
}

export interface PhoneBindingSuggestion {
  phone_e164: string;
  from_number: string;
  company_id: string;
  company_name: string;
  company_display_name: string | null;
  hits: number;
  last_seen_at: string;
}

interface PhoneBindingSuggestionList {
  items: PhoneBindingSuggestion[];
  total: number;
}

const BINDINGS_KEY = ["phone-bindings"] as const;
const SUGGESTIONS_KEY = ["phone-bindings", "suggestions"] as const;

export function usePhoneBindings() {
  return useQuery<PhoneBindingList, ApiError>({
    queryKey: BINDINGS_KEY,
    queryFn: () => apiClient.get<PhoneBindingList>(API_ROUTES.PHONE_BINDINGS),
    staleTime: 60_000,
  });
}

export function usePhoneBindingSuggestions() {
  return useQuery<PhoneBindingSuggestionList, ApiError>({
    queryKey: SUGGESTIONS_KEY,
    queryFn: () =>
      apiClient.get<PhoneBindingSuggestionList>(
        API_ROUTES.PHONE_BINDING_SUGGESTIONS
      ),
    staleTime: 60_000,
  });
}

interface CreateBindingInput {
  phone: string;
  company_id: string;
  note?: string | null;
}

export function useCreatePhoneBinding() {
  const qc = useQueryClient();
  return useMutation<PhoneBinding, ApiError, CreateBindingInput>({
    mutationFn: (input) =>
      apiClient.post<PhoneBinding>(API_ROUTES.PHONE_BINDINGS, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: BINDINGS_KEY });
      qc.invalidateQueries({ queryKey: SUGGESTIONS_KEY });
    },
  });
}

export function useDeletePhoneBinding() {
  const qc = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (id) => apiClient.delete<void>(API_ROUTES.PHONE_BINDING(id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: BINDINGS_KEY });
      qc.invalidateQueries({ queryKey: SUGGESTIONS_KEY });
    },
  });
}
