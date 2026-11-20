import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router'
import {
  Activity, BarChart3, BellRing, BookOpenCheck, ChartCandlestick, FlaskConical, History,
  LayoutDashboard, Menu, Octagon, Pause, Play, Settings, ShieldAlert, Square, X,
} from 'lucide-react'
import { Toaster } from 'sonner'
import { useDashboard } from '@/hooks/use-dashboard'
import { Badge, LoadingBlock, StatusDot } from './common'
import { EmergencyDialog, StartDialog } from './TradingDialogs'

const navigation = [
  { to: '/', label: 'Operations', icon: LayoutDashboard },
  { to: '/markets', label: 'Markets', icon: ChartCandlestick },
  { to: '/positions', label: 'Positions & orders', icon: Activity },
  { to: '/history', label: 'Trade history', icon: History },
  { to: '/performance', label: 'Performance', icon: BarChart3 },
  { to: '/backtests', label: 'Quote replay', icon: FlaskConical },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Layout() {
  const { status, loading, error, control, busy } = useDashboard()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [startOpen, setStartOpen] = useState(false)
  const [emergencyOpen, setEmergencyOpen] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMobileOpen(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [])

  const entries = Boolean(status?.entries_enabled)
  const marketOpen = status?.clock?.is_open
  return (
    <div className="app-shell min-h-dvh">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <aside className="sidebar hidden lg:flex">
        <Brand />
        <div className="px-5 pb-5">
          <div className="mode-block">
            <span className="data-label">Broker environment</span>
            <div className="mt-2 flex items-center justify-between">
              <strong className="text-sm uppercase">{status?.mode ?? 'Paper'}</strong>
              <Badge tone={status?.mode === 'live' ? 'negative' : 'neutral'}>{status?.mode === 'live' ? 'Real funds' : 'Simulated fills'}</Badge>
            </div>
          </div>
        </div>
        <NavItems />
        <div className="mt-auto p-5">
          <div className="border-t border-border pt-4 text-[11px] leading-relaxed text-muted-foreground">
            <ShieldAlert size={16} className="mb-2 text-warning" />
            Protective monitoring continues while new entries are paused.
          </div>
          <button className="mt-4 flex min-h-11 w-full items-center gap-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground" onClick={() => navigate('/settings#configuration')}>
            <BookOpenCheck size={14} />Configuration guide
          </button>
        </div>
      </aside>

      {mobileOpen && <div className="mobile-backdrop lg:hidden" onClick={() => setMobileOpen(false)} aria-hidden="true" />}
      <aside className={`mobile-sidebar lg:hidden ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`} aria-hidden={!mobileOpen}>
        <div className="flex items-center justify-between"><Brand /><button className="icon-button" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={18} /></button></div>
        <NavItems onNavigate={() => setMobileOpen(false)} />
      </aside>

      <div className="lg:pl-[232px]">
        <header className="topbar">
          <div className="flex min-w-0 items-center gap-3">
            <button className="icon-button lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={19} /></button>
            <div className="hidden min-w-0 sm:block">
              <StatusDot status={status?.state === 'running' ? 'live' : status?.state === 'error' || status?.state === 'circuit_breaker' ? 'critical' : 'warning'}
                label={status?.state === 'running' ? 'AUTOMATION ACTIVE' : status?.state === 'setup_required' ? 'SETUP REQUIRED' : status?.state === 'circuit_breaker' ? 'CIRCUIT BREAKER' : status?.state === 'error' ? 'ENGINE ERROR' : 'MONITORING / PAUSED'} />
            </div>
            <span className="hidden text-[10px] uppercase tracking-wider text-muted-foreground xl:inline">{marketOpen === undefined ? 'Session unknown' : marketOpen ? 'Regular session open' : 'Market closed'}</span>
          </div>
          <div className="flex items-center gap-2">
            <button className="button button-primary desktop-control" disabled={!status || busy !== null || status.state === 'circuit_breaker'} onClick={() => entries ? void control('pause') : setStartOpen(true)}>
              {entries ? <Pause size={14} /> : <Play size={14} />}{entries ? 'Pause entries' : 'Start automation'}
            </button>
            <button className="button button-primary mobile-control" disabled={!status || busy !== null} onClick={() => entries ? void control('pause') : setStartOpen(true)} aria-label={entries ? 'Pause entries' : 'Start automation'}>
              {entries ? <Pause size={16} /> : <Play size={16} />}
            </button>
            <button className="icon-button desktop-control" title="Stop entries and cancel opening orders" aria-label="Stop trading" disabled={!status || busy !== null} onClick={() => void control('stop')}><Square size={14} /></button>
            <button className="button button-danger" disabled={!status || busy !== null} onClick={() => setEmergencyOpen(true)}><Octagon size={14} /><span className="hidden md:inline">Liquidate</span></button>
          </div>
        </header>
        <div className="risk-ticker" aria-label="Risk notice">
          <div>REAL-TIME OPRA REQUIRED <span>◆</span> STOPS DO NOT GUARANTEE FILLS <span>◆</span> PAPER AND LIVE RECORDS ARE ISOLATED <span>◆</span> NO PROFITABILITY GUARANTEE</div>
        </div>
        {error && <div className="border-b border-critical/30 bg-critical/10 px-4 py-2 text-xs text-foreground" role="alert">Dashboard API unavailable: {error}</div>}
        <main id="main-content" className="min-w-0 p-4 sm:p-6 xl:p-8">
          {loading && !status ? <LoadingBlock label="Connecting to the trading runtime" /> : <Outlet />}
        </main>
      </div>
      <StartDialog open={startOpen} onOpenChange={setStartOpen} resume={status?.state === 'paused'} />
      <EmergencyDialog open={emergencyOpen} onOpenChange={setEmergencyOpen} />
      <Toaster position="bottom-right" theme="dark" richColors closeButton />
    </div>
  )
}

function Brand() {
  return (
    <div className="flex h-[72px] items-center gap-3 px-5">
      <img src="/icons/icon-192.png" alt="" className="h-8 w-8 rounded-[4px]" />
      <div><strong className="block text-sm font-semibold tracking-[0.12em]">APEX</strong><span className="block text-[9px] uppercase tracking-[0.22em] text-muted-foreground">Options pilot</span></div>
    </div>
  )
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1 px-3" aria-label="Primary navigation">
      <span className="mb-2 px-3 text-[9px] uppercase tracking-[0.18em] text-muted-foreground">Workspace</span>
      {navigation.map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} end={to === '/'} onClick={onNavigate} className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}>
          <Icon size={16} strokeWidth={1.7} /><span>{label}</span>
        </NavLink>
      ))}
      <div className="mt-6 flex items-center gap-2 px-3 text-[10px] text-muted-foreground"><BellRing size={13} />Alerts are server-side</div>
    </nav>
  )
}
