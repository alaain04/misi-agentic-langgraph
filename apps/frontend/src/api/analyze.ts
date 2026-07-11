// src/api/analyze.ts
import { apiClient } from './client'
import type {
  AnalysisRequest,
  StatusResponse,
  SubmitResponse,
} from './types'

export async function submitAnalysis(req: AnalysisRequest): Promise<SubmitResponse> {
  return apiClient.post<SubmitResponse>('/analyze', req)
}

export async function getAnalysisStatus(traceId: string): Promise<StatusResponse> {
  return apiClient.get<StatusResponse>(`/analyze/${traceId}`)
}

export async function sendChatMessage(traceId: string, message: string): Promise<void> {
  return apiClient.post(`/analyze/${traceId}/chat`, { message })
}
