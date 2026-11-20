import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { DashboardProvider } from '@/hooks/use-dashboard'
import Layout from '@/components/Layout'
import { LoadingBlock } from '@/components/common'

const Operations = lazy(() => import('@/pages/Operations'))
const Markets = lazy(() => import('@/pages/Markets'))
const Positions = lazy(() => import('@/pages/Positions'))
const History = lazy(() => import('@/pages/History'))
const Performance = lazy(() => import('@/pages/Performance'))
const Backtests = lazy(() => import('@/pages/Backtests'))
const SettingsPage = lazy(() => import('@/pages/Settings'))

export default function App() {
  return (
    <DashboardProvider>
      <Suspense fallback={<LoadingBlock label="Loading workspace" />}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Operations />} />
            <Route path="/markets" element={<Markets />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/history" element={<History />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </DashboardProvider>
  )
}
