import { apiClient } from './client'
import type { AnalysisRequest, AnalysisResponse, StatusResponse } from './types'

export async function submitAnalysis(payload: AnalysisRequest): Promise<AnalysisResponse> {
  return apiClient.post<AnalysisResponse>('/analyze', payload)
}

export async function getAnalysisStatus(traceId: string): Promise<StatusResponse> {
  return apiClient.get<StatusResponse>(`/analyze/${traceId}`)
}
