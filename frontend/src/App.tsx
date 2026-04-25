import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { PageWrapper } from './components/layout/PageWrapper'
import { Header } from './components/layout/Header'
import { AnalysisPage } from './pages/AnalysisPage'
import JobsListPage from './pages/JobsListPage'
import JobDetailPage from './pages/JobDetailPage'
import PlanPage from './pages/PlanPage'

export function App() {
  return (
    <BrowserRouter>
      <PageWrapper>
        <Header />
        <Routes>
          <Route path="/" element={<AnalysisPage />} />
          <Route path="/jobs" element={<JobsListPage />} />
          <Route path="/jobs/:traceId/plan" element={<PlanPage />} />
          <Route path="/jobs/:traceId" element={<JobDetailPage />} />
        </Routes>
      </PageWrapper>
    </BrowserRouter>
  )
}
