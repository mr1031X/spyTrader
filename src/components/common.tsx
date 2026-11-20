import type { ReactNode } from 'react'
import { AlertCircle, Inbox, LoaderCircle, RefreshCw } from 'lucide-react'

export function Panel({ title, subtitle, action, children, className = '', id }: {
  title: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string; id?: string
}) {
  return (
    <section id={id} className={`panel ${className}`}>
      <header className="panel-header">
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid-marker" aria-hidden="true"><i /><i /><i /><i /></span>
          <div className="min-w-0">
            <h2 className="panel-title">{title}</h2>
            {subtitle && <p className="panel-subtitle">{subtitle}</p>}
          </div>
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  )
}

export function PageHeader({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="page-title">{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {action}
    </div>
  )
}

export function Metric({ label, value, detail, tone = 'default' }: {
  label: string; value: ReactNode; detail?: ReactNode; tone?: 'default' | 'positive' | 'negative' | 'warning'
}) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <strong className={`metric-value tone-${tone}`}>{value}</strong>
      {detail && <span className="metric-detail">{detail}</span>}
    </div>
  )
}

export function StatusDot({ status = 'neutral', label }: { status?: 'live' | 'warning' | 'critical' | 'neutral'; label: string }) {
  return <span className={`status-dot status-${status}`}><i aria-hidden="true" />{label}</span>
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <Inbox size={20} strokeWidth={1.5} aria-hidden="true" />
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  )
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="empty-state border-critical/30" role="alert">
      <AlertCircle size={20} className="text-critical" aria-hidden="true" />
      <strong>Data unavailable</strong>
      <p>{message}</p>
      {retry && <button className="button button-secondary" onClick={retry}><RefreshCw size={14} />Retry</button>}
    </div>
  )
}

export function LoadingBlock({ label = 'Loading live state' }: { label?: string }) {
  return <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="animate-spin" size={16} />{label}</div>
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'positive' | 'negative' | 'warning' | 'active' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function DataLabel({ children }: { children: ReactNode }) {
  return <span className="data-label">{children}</span>
}
