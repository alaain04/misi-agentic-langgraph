// src/App.tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { PageWrapper } from './components/layout/PageWrapper'
import { Header } from './components/layout/Header'
import { LandingPage } from './pages/LandingPage'
import NewAnalysisPage from './pages/NewAnalysisPage'
import JobsListPage from './pages/JobsListPage'
import ExecutionPage from './pages/ExecutionPage'
import ReportPage from './pages/ReportPage'

export function App() {
  return (
    <BrowserRouter>
      <PageWrapper>
        <Header />
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/new" element={<NewAnalysisPage />} />
          <Route path="/jobs" element={<JobsListPage />} />
          <Route path="/jobs/:traceId" element={<ExecutionPage />} />
          <Route path="/jobs/:traceId/report" element={<ReportPage />} />
        </Routes>
      </PageWrapper>
    </BrowserRouter>
  )
}
