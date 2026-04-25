import type { JobsListResponse, StatusResponse } from './types'

const BASE_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API ${status}: ${detail}`)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    ...init,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // ignore parse failure
    }
    throw new ApiError(response.status, detail)
  }

  return response.json() as Promise<T>
}

export const apiClient = {
  post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },
  get<T>(path: string): Promise<T> {
    return request<T>(path, { method: 'GET' })
  },
}

export function getJobs(
  page = 1,
  limit = 10,
  status?: string,
  traceId?: string,
): Promise<JobsListResponse> {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) })
  if (status) params.set('status', status)
  if (traceId) params.set('trace_id', traceId)
  return apiClient.get<JobsListResponse>(`/jobs?${params.toString()}`)
}

export function getJobStatus(traceId: string): Promise<StatusResponse> {
  return apiClient.get<StatusResponse>(`/analyze/${traceId}`)
}
