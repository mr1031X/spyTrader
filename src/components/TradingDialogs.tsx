import { useState } from 'react'
import { AlertTriangle, ShieldCheck } from 'lucide-react'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { useDashboard } from '@/hooks/use-dashboard'

export function StartDialog({ open, onOpenChange, resume }: { open: boolean; onOpenChange: (value: boolean) => void; resume: boolean }) {
  const { control, busy, status } = useDashboard()
  const [acknowledged, setAcknowledged] = useState(false)
  const changeOpen = (value: boolean) => {
    if (!value) setAcknowledged(false)
    onOpenChange(value)
  }
  const run = async () => {
    if (await control(resume ? 'resume' : 'start', acknowledged)) changeOpen(false)
  }
  return (
    <AlertDialog open={open} onOpenChange={changeOpen}>
      <AlertDialogContent className="terminal-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>{resume ? 'Resume automated entries?' : 'Arm automated paper trading?'}</AlertDialogTitle>
          <AlertDialogDescription>
            Apex will evaluate {status?.settings.symbols.join(' and ') || 'configured underlyings'}, select 1–3 DTE long options, and submit real Alpaca {status?.mode || 'paper'} orders when all deterministic gates pass.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2 border-y border-border py-4 text-xs leading-relaxed text-muted-foreground">
          <p>Up to {status?.settings.max_trades_per_day ?? 3} completed trades per day; one open position.</p>
          <p>Stop triggers do not guarantee execution prices. Entire option premium remains at risk.</p>
        </div>
        <label className="flex min-h-11 cursor-pointer items-start gap-3 text-sm">
          <input type="checkbox" className="mt-1 h-4 w-4 accent-[#22d3ee]" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
          <span>I reviewed the risk settings and authorize automated {status?.mode || 'paper'} entries.</span>
        </label>
        <AlertDialogFooter>
          <AlertDialogCancel className="button button-secondary">Cancel</AlertDialogCancel>
          <AlertDialogAction className="button button-primary" disabled={!acknowledged || busy !== null} onClick={(event) => { event.preventDefault(); void run() }}>
            <ShieldCheck size={14} />{busy ? 'Running preflight…' : 'Enable entries'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function EmergencyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (value: boolean) => void }) {
  const { emergency, busy } = useDashboard()
  const [confirmation, setConfirmation] = useState('')
  const changeOpen = (value: boolean) => {
    if (!value) setConfirmation('')
    onOpenChange(value)
  }
  const run = async () => { if (await emergency()) changeOpen(false) }
  return (
    <AlertDialog open={open} onOpenChange={changeOpen}>
      <AlertDialogContent className="terminal-dialog border-critical/50">
        <AlertDialogHeader>
          <AlertTriangle className="mb-2 text-critical" size={24} />
          <AlertDialogTitle>Emergency liquidation</AlertDialogTitle>
          <AlertDialogDescription>
            This pauses entries, cancels opening orders and conflicting exits, then requests broker liquidation of all managed open positions. Actual fills and slippage are not guaranteed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <label className="form-label" htmlFor="emergency-confirmation">Type <strong className="text-critical">LIQUIDATE NOW</strong> to confirm</label>
        <input id="emergency-confirmation" className="form-input" autoComplete="off" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <AlertDialogFooter>
          <AlertDialogCancel className="button button-secondary">Cancel</AlertDialogCancel>
          <AlertDialogAction className="button button-danger" disabled={confirmation !== 'LIQUIDATE NOW' || busy !== null} onClick={(event) => { event.preventDefault(); void run() }}>
            {busy ? 'Submitting…' : 'Liquidate all managed positions'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
