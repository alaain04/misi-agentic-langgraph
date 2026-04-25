import { apiClient } from './client'
import type { AnalysisRequest, AnalysisResponse, StatusResponse, PlanApprovalRequest } from './types'

export async function submitAnalysis(payload: AnalysisRequest): Promise<AnalysisResponse> {
  return apiClient.post<AnalysisResponse>('/analyze', payload)
}

export async function getAnalysisStatus(traceId: string): Promise<StatusResponse> {
  return apiClient.get<StatusResponse>(`/analyze/${traceId}`)
}

export async function approvePlan(traceId: string, payload: PlanApprovalRequest): Promise<void> {
  return apiClient.post(`/analyze/${traceId}/approve`, payload)
}
