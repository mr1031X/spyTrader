import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { post, put } from '@/lib/api'
import { usePolling } from '@/hooks/use-polling'
import type { Settings, Status } from '@/types'

interface DashboardContextValue {
  status: Status | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  busy: string | null
  control: (action: 'start' | 'pause' | 'resume' | 'stop', acknowledge?: boolean) => Promise<boolean>
  saveSettings: (settings: Settings) => Promise<boolean>
  emergency: () => Promise<boolean>
}

const DashboardContext = createContext<DashboardContextValue | null>(null)

export function DashboardProvider({ children }: { children: ReactNode }) {
  const { data, loading, error, refresh } = usePolling<Status>('/status', 3000)
  const [busy, setBusy] = useState<string | null>(null)

  const value = useMemo<DashboardContextValue>(() => ({
    status: data, loading, error, refresh, busy,
    control: async (action, acknowledge = false) => {
      setBusy(action)
      try {
        await post(`/control/${action}`, { acknowledge_risk: acknowledge })
        toast.success({ start: 'Automated entries enabled', resume: 'Automated entries resumed', pause: 'New entries paused; protection remains active', stop: 'Opening orders canceled; monitoring continues' }[action])
        await refresh()
        return true
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : 'Control request failed')
        return false
      } finally { setBusy(null) }
    },
    saveSettings: async (settings) => {
      setBusy('settings')
      try {
        await put('/settings', settings)
        toast.success('Settings saved. Active-position protection was not changed.')
        await refresh()
        return true
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : 'Could not save settings')
        return false
      } finally { setBusy(null) }
    },
    emergency: async () => {
      setBusy('emergency')
      try {
        await post('/emergency', { confirmation: 'LIQUIDATE NOW' })
        toast.success('Emergency liquidation orders submitted; awaiting broker confirmation')
        await refresh()
        return true
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : 'Emergency request failed')
        return false
      } finally { setBusy(null) }
    },
  }), [data, loading, error, refresh, busy])

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>
}

export function useDashboard() {
  const value = useContext(DashboardContext)
  if (!value) throw new Error('useDashboard must be used inside DashboardProvider')
  return value
}
