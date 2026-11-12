import { useState } from "react"
import { toast } from "sonner"
import { ArrowRight, Bot, ExternalLink, ThumbsDown, ShieldCheck, Loader2, MapPin } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, errText, usePoll, rub, ddmm, ago, atiLink, type Load, type Truck } from "@/lib/api"
import { cn } from "@/lib/utils"

const VIEWS = [
  { id: "relevant", label: "Подходят" },
  { id: "filtered", label: "Отсеяно ботом" },
  { id: "rejected", label: "Отклонил логист" },
]

export default function LoadsPage() {
  const [view, setView] = useState("relevant")
  const [truck, setTruck] = useState("all")
  const { data: loads, reload } = usePoll<Load[]>(`/loads?view=${view}${truck !== "all" ? `&truck=${truck}` : ""}`, 6000)
  const { data: trucks } = usePoll<Truck[]>("/trucks", 15000)
  const [rejecting, setRejecting] = useState<Load | null>(null)
  const [delegating, setDelegating] = useState<Load | null>(null)
  const tmap = Object.fromEntries((trucks ?? []).map((t) => [t.id, t]))

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Грузы с биржи АТИ</h1>
          <p className="text-sm text-muted-foreground">Ассистент сам смотрит биржу под каждую свободную машину и оставляет только то, что подходит по вашим правилам.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={truck} onValueChange={setTruck}>
            <SelectTrigger className="w-56 bg-card"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Все машины</SelectItem>
              {trucks?.map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name} · {t.city_name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Tabs value={view} onValueChange={setView}>
            <TabsList>{VIEWS.map((v) => <TabsTrigger key={v.id} value={v.id}>{v.label}</TabsTrigger>)}</TabsList>
          </Tabs>
        </div>
      </div>

      {!loads ? <Loader2 className="mx-auto size-6 animate-spin text-muted-foreground" /> :
        loads.length === 0 ? <div className="rounded-xl border border-dashed bg-card p-10 text-center text-sm text-muted-foreground">Пока пусто — ассистент проходит биржу каждые несколько минут.</div> :
          <div className="flex flex-col gap-3">
            {loads.map((l) => <LoadCard key={l.id} l={l} truck={tmap[l.truck_id]} view={view}
              onReject={() => setRejecting(l)} onDelegate={() => setDelegating(l)} />)}
          </div>}

      <RejectDialog load={rejecting} onClose={() => setRejecting(null)} onDone={reload} />
      <DelegateDialog load={delegating} onClose={() => setDelegating(null)} onDone={reload} />
    </div>
  )
}

function ScoreRing({ v, filtered }: { v: number; filtered: boolean }) {
  const color = filtered ? "text-muted-foreground" : v >= 80 ? "text-emerald-600" : v >= 65 ? "text-primary" : "text-amber-600"
  return (
    <div className={cn("relative grid size-14 shrink-0 place-items-center", color)}>
      <svg viewBox="0 0 36 36" className="absolute inset-0 -rotate-90">
        <circle cx="18" cy="18" r="15.5" fill="none" className="stroke-muted" strokeWidth="3" />
        <circle cx="18" cy="18" r="15.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeDasharray={`${v * 0.974} 100`} />
      </svg>
      <span className="num text-base font-semibold">{v}</span>
    </div>
  )
}

function LoadCard({ l, truck, view, onReject, onDelegate }: { l: Load; truck?: Truck; view: string; onReject: () => void; onDelegate: () => void }) {
  const r = l.raw, m = l.metrics, f = r.firm
  const blocks = l.reasons.filter((x) => x.kind === "block")
  const shown = view === "filtered" && blocks.length ? blocks : l.reasons.filter((x) => x.kind !== "info").sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 5)
  return (
    <div className="rounded-xl border bg-card p-4 shadow-xs transition-shadow hover:shadow-sm">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
        <ScoreRing v={l.score} filtered={l.verdict === "filtered"} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-base font-semibold">
            <span>{r.loading.location.city}</span><ArrowRight className="size-4 text-muted-foreground" /><span>{r.unloading.location.city}</span>
            <span className="num text-sm font-normal text-muted-foreground">{m.dist} км</span>
            {r.loadNumber && <span className="text-xs font-normal text-muted-foreground">#{r.loadNumber}</span>}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-sm text-muted-foreground">
            <span>погрузка {ddmm(r.loading.firstDate)}{r.loading.lastDate !== r.loading.firstDate && `–${ddmm(r.loading.lastDate)}`}</span>
            <span>{r.load.cargoType}, {r.load.weight} т{r.load.volume ? `, ${r.load.volume} м³` : ""}</span>
            {truck && <span className="inline-flex items-center gap-1"><MapPin className="size-3.5" />{truck.name.split(" · ")[0]} в {m.dead} км</span>}
          </div>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {shown.map((x, i) => (
              <span key={i} className={cn("rounded-md px-2 py-0.5 text-xs",
                x.kind === "plus" && "bg-emerald-50 text-emerald-700",
                x.kind === "minus" && "bg-amber-50 text-amber-800",
                x.kind === "block" && "bg-red-50 text-red-700")}>{x.t}</span>
            ))}
          </div>
          {l.reject_reason && <div className="mt-2 text-sm text-muted-foreground">Логист: «{l.reject_reason}»</div>}
        </div>

        <div className="flex shrink-0 items-center gap-5 lg:w-[430px] lg:justify-end">
          <div className="text-right">
            <div className="num text-lg font-semibold">{m.gross ? rub(m.gross) : <span className="text-muted-foreground">запрос</span>}</div>
            <div className="text-xs text-muted-foreground">{m.per_km ? `${m.per_km} ₽/км чистыми · ` : ""}{m.money}</div>
          </div>
          <div className="w-40 text-sm">
            <div className="truncate font-medium" title={f?.name}>{f?.name ?? "Заказчик скрыт"}</div>
            <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
              {f?.isConfirmedShipper && <Tooltip><TooltipTrigger><ShieldCheck className="size-3.5 text-emerald-600" /></TooltipTrigger><TooltipContent>Подтверждённый грузовладелец</TooltipContent></Tooltip>}
              {f?.profile && <span>{f.profile}</span>}
              {f?.rating && <span>· {f.rating.score}★</span>}
              <span>· {ago(Date.parse(r.changeDate ?? r.addDate) / 1000)}</span>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            {view !== "rejected" && <Button size="sm" onClick={onDelegate} disabled={!f?.contacts?.length}><Bot />Поручить агенту</Button>}
            <div className="flex gap-1.5">
              {view !== "rejected" && <Button size="sm" variant="outline" onClick={onReject}><ThumbsDown />Не то</Button>}
              <Button size="sm" variant="ghost" asChild><a href={atiLink(r.id)} target="_blank" rel="noreferrer"><ExternalLink />АТИ</a></Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function RejectDialog({ load, onClose, onDone }: { load: Load | null; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("")
  const [learn, setLearn] = useState(true)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!load) return
    setBusy(true)
    try {
      const res = await api<{ rule: { text: string } | null }>(`/loads/${load.id}/reject`, { body: { reason, learn } })
      toast.success("Груз убран", { description: res.rule ? `В базу знаний: «${res.rule.text}»` : undefined })
      setReason(""); onClose(); onDone()
    } catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Dialog open={!!load} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Почему не подходит?</DialogTitle>
          <DialogDescription>{load && `${load.raw.loading.location.city} → ${load.raw.unloading.location.city}, ${load.raw.load.cargoType}`}. Напишите как коллеге — ассистент превратит это в правило и больше не будет такое предлагать.</DialogDescription>
        </DialogHeader>
        <Textarea id="reject-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="Например: с этим экспедитором не работаем — тянут с оплатой по 60 дней" />
        <div className="flex flex-wrap gap-1.5">
          {["Эта фирма — посредник-мусорщик", "Кирпич не возим, рвёт тент", "В этот город не едем — нет обратки"].map((s) => (
            <button key={s} onClick={() => setReason(s)} className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted">{s}</button>
          ))}
        </div>
        <div className="flex items-center gap-2"><Switch id="learn" checked={learn} onCheckedChange={setLearn} /><Label htmlFor="learn">Запомнить в базе знаний</Label></div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Отмена</Button>
          <Button onClick={submit} disabled={busy || (learn && !reason.trim())}>{busy && <Loader2 className="animate-spin" />}{busy ? "Ассистент учится…" : "Убрать груз"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DelegateDialog({ load, onClose, onDone }: { load: Load | null; onClose: () => void; onDone: () => void }) {
  const { data: st } = usePoll<{ default_goal: string }>("/settings", 60000)
  const [goal, setGoal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const contact = load?.raw.firm?.contacts?.[0]
  const submit = async () => {
    if (!load) return
    setBusy(true)
    try {
      await api(`/loads/${load.id}/agent`, { body: { goal: goal ?? st?.default_goal } })
      toast.success("Агент взял груз в работу", { description: "Переписка — во вкладке «Переговоры»" })
      setGoal(null); onClose(); onDone()
    } catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Dialog open={!!load} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Поручить переговоры агенту</DialogTitle>
          <DialogDescription>
            Агент напишет {contact ? <b className="text-foreground">{contact.name}</b> : "заказчику"} в мессенджер АТИ от вашего имени и уточнит то, что нужно по сценарию. Если не ответят в чате — позвонит (когда звонки включены). Сам ничего не подтверждает: позовёт вас, когда всё будет известно.
          </DialogDescription>
        </DialogHeader>
        <Label htmlFor="goal">Сценарий: что узнать</Label>
        <Textarea id="goal" rows={4} value={goal ?? st?.default_goal ?? ""} onChange={(e) => setGoal(e.target.value)} />
        <div className="flex flex-wrap gap-1.5">
          {["Узнать, актуален ли груз и можно ли поднять ставку до 60 ₽/км", "Уточнить точный адрес, время погрузки и нужна ли доверенность", "Предложить нашу машину и спросить про обратную загрузку"].map((s) => (
            <button key={s} onClick={() => setGoal(s)} className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted">{s}</button>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Отмена</Button>
          <Button onClick={submit} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <Bot />}Начать переписку</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
