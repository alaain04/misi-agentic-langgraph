import { apiClient } from './client'
import type { AnalysisRequest, AnalysisResponse, StatusResponse } from './types'

export async function submitAnalysis(payload: AnalysisRequest): Promise<AnalysisResponse> {
  return apiClient.post<AnalysisResponse>('/analyze', payload)
}

export async function getAnalysisStatus(traceId: string): Promise<StatusResponse> {
  return apiClient.get<StatusResponse>(`/analyze/${traceId}`)
}

export async function sendChatMessage(traceId: string, message: string): Promise<void> {
  return apiClient.post(`/analyze/${traceId}/chat`, { message })
}

export async function approvePlan(
  traceId: string,
  payload: {
    action: 'approve' | 'modify' | 'cancel' | 'refine'
    plan?: string[]
    feedback?: string
  },
): Promise<void> {
  return apiClient.post(`/analyze/${traceId}/approve`, payload)
}
