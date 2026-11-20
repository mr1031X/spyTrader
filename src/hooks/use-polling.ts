import { useCallback, useEffect, useRef, useState } from 'react'
import { get } from '@/lib/api'

export function usePolling<T>(path: string | null, interval = 5000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(Boolean(path))
  const mounted = useRef(true)

  const refresh = useCallback(async () => {
    if (!path) return
    try {
      const result = await get<T>(path)
      if (!mounted.current) return
      setData(result)
      setError(null)
    } catch (reason) {
      if (!mounted.current) return
      setError(reason instanceof Error ? reason.message : 'Request failed')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [path])

  useEffect(() => {
    mounted.current = true
    setLoading(Boolean(path))
    void refresh()
    if (!path || interval <= 0) return () => { mounted.current = false }
    const timer = window.setInterval(() => void refresh(), interval)
    return () => {
      mounted.current = false
      window.clearInterval(timer)
    }
  }, [refresh, interval, path])

  return { data, error, loading, refresh, setData }
}
