import { useState } from "react"
import { toast } from "sonner"
import { ArrowRight, Bot, ExternalLink, ThumbsDown, ShieldCheck, Loader2, MapPin, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { CityInput } from "@/components/CityInput"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, errText, usePoll, rub, ddmm, ago, atiLink, type Load, type Truck } from "@/lib/api"
import { cn } from "@/lib/utils"
import SearchPanel, { LIST_HEAD, TOOLBAR, type Profile, type SearchResult } from "@/components/SearchPanel"

const VIEWS = [
  { id: "relevant", label: "Подходят" },
  { id: "filtered", label: "Отсеяно ботом" },
  { id: "rejected", label: "Отклонил логист" },
]

export default function LoadsPage() {
  const [mode, setMode] = useState<"general" | "trucks">("general")
  const [general, setGeneral] = useState<SearchResult | null>(null)
  const [manual, setManual] = useState<Partial<ManualForm> | null>(null)
  const [view, setView] = useState("relevant")
  const [truck, setTruck] = useState("all")
  const { data: loads, reload } = usePoll<Load[]>(`/loads?view=${view}${truck !== "all" ? `&truck=${truck}` : ""}`, 6000)
  const { data: trucks } = usePoll<Truck[]>("/trucks", 15000)
  const [rejecting, setRejecting] = useState<Load | null>(null)
  const [delegating, setDelegating] = useState<Load | null>(null)
  const [taking, setTaking] = useState<Load | null>(null)  // груз из общего поиска ждёт выбора машины
  // общий поиск → груз в работу под машину → то же окно «Поручить агенту», что и у грузов под машину
  const take = async (load: Load, truckId: number) => {
    setTaking(null)
    try { setDelegating(await api<Load>("/loads/take", { body: { load_id: load.id, truck_id: truckId } })) }
    catch (e) { toast.error(errText(e)) }
  }
  const delegateGeneral = (load: Load) => {
    const free = (trucks ?? []).filter((t) => t.active)
    if (free.length === 1) void take(load, free[0].id)
    else if (free.length === 0) toast.error("Сначала добавьте машину в разделе «Машины»")
    else setTaking(load)
  }
  const tmap = Object.fromEntries((trucks ?? []).map((t) => [t.id, t]))

  return (
    <div className="flex flex-col gap-5">
      {/* Шапка, переключатель и строка инструментов одинаковы в обоих режимах: при переключении список не прыгает */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Грузы с биржи АТИ</h1>
        <p className="text-sm text-muted-foreground">Грузы упорядочены по оценке ассистента: в общем поиске — по вашим фильтрам, под машины — ассистент сам смотрит биржу под каждую свободную машину.</p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs value={mode} onValueChange={(value) => setMode(value as "general" | "trucks")}>
          <TabsList><TabsTrigger value="general">Общий поиск</TabsTrigger><TabsTrigger value="trucks">Под мои машины</TabsTrigger></TabsList>
        </Tabs>
        <Button variant="outline" onClick={() => setManual({})}><Plus />Добавить груз вручную</Button>
      </div>

      <div className={cn("flex flex-col gap-3", mode !== "general" && "hidden")}><SearchPanel onResults={setGeneral} onDefaultMode={setMode} /></div>
      {mode === "trucks" && <div className="flex flex-col gap-3">
        <div className={cn(TOOLBAR, "grid gap-2 sm:flex sm:flex-wrap")}>
          <Select value={truck} onValueChange={setTruck}>
            <SelectTrigger className="w-full bg-card sm:w-56"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Все машины</SelectItem>
              {trucks?.map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name} · {t.city_name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Tabs value={view} onValueChange={setView}>
            <TabsList className="w-full sm:w-auto">{VIEWS.map((v) => <TabsTrigger key={v.id} value={v.id}>{v.label}</TabsTrigger>)}</TabsList>
          </Tabs>
        </div>
        <div className={LIST_HEAD}>{loads ? `Грузов: ${loads.length}` : "Загружаем…"}</div>
      </div>}
      {mode === "general" && <>
        {general?.public_only && <p className="text-sm text-muted-foreground">АТИ не передаёт сюда ставки и заказчиков. Сумму смотрите кнопкой «АТИ» и впишите — ассистент посчитает выгоду.</p>}
        {general && (general.loads.length === 0 ? <p className="text-sm text-muted-foreground">По этим фильтрам грузов нет. Попробуйте увеличить радиус или выбрать другой кузов.</p> : general.loads.map((load) => <LoadCard key={load.id} l={load} view="all" general onReject={() => {}} onDelegate={() => delegateGeneral(load)} profile={general.profile}
          onComplete={load.raw.firm?.contacts?.length ? undefined : (rate) => setManual({ ...fromLoad(load), ...rate })} />))}
      </>}

      {mode === "trucks" && (!loads ? <Loader2 className="mx-auto size-6 animate-spin text-muted-foreground" /> :
        loads.length === 0 ? <div className="rounded-xl border border-dashed bg-card p-10 text-center text-sm text-muted-foreground">{trucks?.length === 0 ? "Добавьте машину в разделе «Машины», чтобы запустить поиск грузов." : "Подходящих грузов пока нет. Ассистент повторяет поиск каждые несколько минут."}</div> :
          <div className="flex flex-col gap-3">
            {loads.map((l) => <LoadCard key={l.id} l={l} truck={tmap[l.truck_id]} view={view}
              onReject={() => setRejecting(l)} onDelegate={() => setDelegating(l)} />)}
          </div>)}

      <RejectDialog load={rejecting} onClose={() => setRejecting(null)} onDone={reload} />
      <Dialog open={!!taking} onOpenChange={(o) => { if (!o) setTaking(null) }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Под какую машину?</DialogTitle><DialogDescription>Ассистент оценит груз под выбранную машину, дальше — сценарий для агента.</DialogDescription></DialogHeader>
          <div className="flex flex-col gap-2">
            {(trucks ?? []).filter((t) => t.active).map((t) => <Button key={t.id} variant="outline" className="justify-start" onClick={() => taking && take(taking, t.id)}>{t.name} · {t.city_name}</Button>)}
          </div>
        </DialogContent>
      </Dialog>
      <DelegateDialog load={delegating} onClose={() => setDelegating(null)} onDone={reload} />
      {manual && <ManualLoadDialog init={manual} trucks={trucks ?? []} onClose={() => setManual(null)}
        onDone={(l) => { setManual(null); setMode("trucks"); setTruck("all"); setView(l.verdict === "filtered" ? "filtered" : "relevant"); void reload() }} />}
    </div>
  )
}

type ManualForm = { truck_id: number; from_city: string; to_city: string; load_date: string; weight: number; cargo: string; price: number; money: number; distance: number; firm: string; contact: string; phone: string; note: string }
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10)
const fromLoad = (l: Load): Partial<ManualForm> => ({
  from_city: l.raw.loading.location.city, to_city: l.raw.unloading.location.city, load_date: l.raw.loading.firstDate?.slice(0, 10) || today(),
  weight: l.raw.load.weight, cargo: l.raw.load.cargoType, distance: Math.round(l.raw.route?.distance || 0), note: l.raw.note ?? "",
})

function ManualLoadDialog({ init, trucks, onClose, onDone }: { init: Partial<ManualForm>; trucks: Truck[]; onClose: () => void; onDone: (l: Load) => void }) {
  const [f, setF] = useState<ManualForm>({ truck_id: trucks[0]?.id ?? 0, from_city: "", to_city: "", load_date: today(), weight: 20, cargo: "", price: 0, money: 1, distance: 0, firm: "", contact: "", phone: "", note: "", ...init })
  const [busy, setBusy] = useState(false)
  const [ad, setAd] = useState("")
  const [parsing, setParsing] = useState(false)
  const [missing, setMissing] = useState<string[] | null>(null)
  const parse = async () => {
    setParsing(true)
    try {
      const r = await api<{ fields: Partial<ManualForm>; missing: string[] }>("/loads/parse", { body: { text: ad } })
      setF((old) => ({ ...old, price: 0, phone: "", ...r.fields })); setMissing(r.missing)  // чужие ставка и телефон от прошлого текста не остаются
    } catch (e) { toast.error(errText(e)) } finally { setParsing(false) }
  }
  const submit = async () => {
    setBusy(true)
    try {
      const l = await api<Load>("/loads/manual", { body: f })
      toast.success(`Груз оценён: ${l.score} из 100`, { description: l.verdict === "filtered" ? "Отсеян — причины в карточке" : undefined })
      onDone(l)
    } catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  const text = (k: "from_city" | "to_city" | "cargo" | "firm" | "contact" | "phone", label: string, placeholder = "") => (
    <div className="grid gap-1.5"><Label htmlFor={`m-${k}`}>{label}</Label>{k === "from_city" || k === "to_city"
      ? <CityInput id={`m-${k}`} value={f[k]} placeholder={placeholder} onChange={(v) => setF({ ...f, [k]: v })} />
      : <Input id={`m-${k}`} value={f[k]} placeholder={placeholder} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</div>)
  const num = (k: "weight" | "price" | "distance", label: string) => (
    <div className="grid gap-1.5"><Label htmlFor={`m-${k}`}>{label}</Label><Input id={`m-${k}`} type="number" min={0} value={f[k] || ""} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} /></div>)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Груз вручную</DialogTitle>
          <DialogDescription>Впишите ставку и телефон, которые видите на АТИ: ассистент оценит груз под машину, а робот сможет позвонить заказчику.</DialogDescription>
        </DialogHeader>
        {trucks.length === 0 ? <p role="alert" className="text-sm text-destructive">Сначала добавьте машину в разделе «Машины»: груз оценивается под неё.</p> : <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5 sm:col-span-2"><Label htmlFor="m-ad">Текст объявления с АТИ</Label>
            <Textarea id="m-ad" rows={3} className="max-h-40" maxLength={6000} value={ad} placeholder="Откройте груз на АТИ, выделите текст, скопируйте и вставьте сюда — поля заполнятся сами" onChange={(e) => setAd(e.target.value)} />
            <div className="flex flex-wrap items-center gap-2"><Button type="button" size="sm" variant="outline" onClick={parse} disabled={parsing || ad.trim().length < 10}>{parsing ? <Loader2 className="animate-spin" /> : <Bot />}Разобрать</Button>
              {missing && <span role="status" className="text-xs text-muted-foreground">{missing.length ? `Не нашёл: ${missing.join(", ")}. Проверьте поля.` : "Поля заполнены — проверьте их."}</span>}</div></div>
          <div className="grid gap-1.5 sm:col-span-2"><Label htmlFor="m-truck">Машина</Label>
            <select id="m-truck" className="h-9 rounded-md border bg-background px-3 text-sm" value={f.truck_id} onChange={(e) => setF({ ...f, truck_id: Number(e.target.value) })}>{trucks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></div>
          {text("from_city", "Откуда")}{text("to_city", "Куда")}
          <div className="grid gap-1.5"><Label htmlFor="m-date">Погрузка</Label><Input id="m-date" type="date" value={f.load_date} onChange={(e) => setF({ ...f, load_date: e.target.value })} /></div>
          {num("weight", "Вес, т")}{text("cargo", "Груз", "ТНП")}{num("distance", "Расстояние, км (пусто — посчитаем)")}
          {num("price", "Ставка, ₽ (пусто — неизвестна)")}
          <div className="grid gap-1.5"><Label htmlFor="m-money">Оплата</Label>
            <select id="m-money" className="h-9 rounded-md border bg-background px-3 text-sm" value={f.money} onChange={(e) => setF({ ...f, money: Number(e.target.value) })}><option value={1}>с НДС</option><option value={2}>без НДС</option><option value={3}>наличными</option><option value={23}>на карту</option></select></div>
          {text("firm", "Фирма")}{text("contact", "Контакт")}{text("phone", "Телефон", "+7 921 123-45-67")}
        </div>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Отмена</Button>
          <Button onClick={submit} disabled={busy || !f.truck_id || !f.from_city.trim() || !f.to_city.trim() || !f.load_date || !f.weight}>{busy ? <Loader2 className="animate-spin" /> : <Plus />}Оценить груз</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ScoreRing({ v, filtered, partial, asked }: { v: number; filtered: boolean; partial?: boolean; asked?: boolean }) {
  if (partial) return (
    <div className="grid size-14 shrink-0 place-items-center rounded-full border-2 border-dashed text-center text-[11px] leading-tight text-muted-foreground" title={asked ? "Заказчик не указал ставку — её надо спросить" : "АТИ показывает ставку только на сайте. Впишите её — груз пересчитается."}>{asked ? "запрос" : <>нет<br />ставки</>}</div>
  )
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

function LoadCard({ l: orig, truck, view, onReject, onDelegate, onComplete, profile, general = false }: { l: Load; profile?: Profile; truck?: Truck; view: string; general?: boolean; onReject: () => void; onDelegate: () => void; onComplete?: (rate: { price?: number; money?: number }) => void }) {
  const [own, setOwn] = useState<{ id: string; price: number; money: number; res: Pick<Load, "score" | "verdict" | "reasons" | "metrics"> } | null>(null)
  const mine = own?.id === orig.id ? own : null  // ставка логиста — только для этой карточки
  const l = mine ? { ...orig, ...mine.res } : orig
  const r = l.raw, m = l.metrics, f = r.firm
  const blocks = l.reasons.filter((x) => x.kind === "block")
  const shown = view === "filtered" && blocks.length ? blocks : l.reasons.filter((x) => x.kind !== "info").sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 5)
  return (
    <div className="rounded-xl border bg-card p-4 shadow-xs transition-shadow hover:shadow-sm">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
        <ScoreRing v={l.score} filtered={l.verdict === "filtered"} partial={m.partial && !blocks.length} asked={m.asked} />
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

        <div className="flex flex-wrap items-center gap-x-5 gap-y-3 lg:w-[430px] lg:shrink-0 lg:flex-nowrap lg:justify-end">
          <div className="text-right">
            {m.partial && profile ? <div className="flex flex-col gap-1"><RateForm load={orig} profile={profile} onRated={setOwn} /><PayInfo l={l} /></div> : m.partial ? <div className="text-sm text-muted-foreground">{m.asked ? "Ставка по запросу" : "Ставка на АТИ"}<PayInfo l={l} /></div> : <>
              <div className="num text-lg font-semibold">{m.gross ? rub(m.gross) : <span className="text-muted-foreground">запрос</span>}</div>
              <div className="text-xs text-muted-foreground">{m.per_km ? `${m.per_km} ₽/км чистыми · ` : ""}{m.money}{m.pay ? `, ${m.pay}` : ""}</div>
              {mine && <button type="button" className="whitespace-nowrap text-xs text-primary hover:underline" onClick={() => setOwn(null)}>изменить ставку</button>}
            </>}
          </div>
          <div className="w-40 text-sm">
            <div className="truncate font-medium" title={f?.name}>{f?.name ?? (r.publicOnly ? "Заказчик на АТИ" : "Заказчик скрыт")}</div>
            <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
              {f?.isConfirmedShipper && <Tooltip><TooltipTrigger><ShieldCheck className="size-3.5 text-emerald-600" /></TooltipTrigger><TooltipContent>Подтверждённый грузовладелец</TooltipContent></Tooltip>}
              {f?.profile && <span>{f.profile}</span>}
              {f?.rating && <span>· {f.rating.score}★</span>}
              <span>· {ago(Date.parse(r.changeDate ?? r.addDate) / 1000)}</span>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            {view !== "rejected" && (!general || !!f?.contacts?.length) && <Button size="sm" onClick={onDelegate} disabled={!f?.contacts?.length} title={f?.contacts?.length ? undefined : "Заказчик скрыт — написать некому, откройте груз на АТИ"}><Bot />Поручить агенту</Button>}
            <div className="flex gap-1.5">
              {!general && view !== "rejected" && <Button size="sm" variant="outline" onClick={onReject}><ThumbsDown />Не то</Button>}
              {onComplete && <Button size="sm" variant="outline" onClick={() => onComplete(mine ? { price: mine.price, money: mine.money } : {})} title="Вписать фирму и телефон с АТИ и взять груз в работу под свою машину"><Plus />В работу</Button>}
              {!r.manual && <Button size="sm" variant="ghost" asChild><a href={atiLink(r.id)} target="_blank" rel="noreferrer"><ExternalLink />АТИ</a></Button>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function PayInfo({ l }: { l: Load }) {
  const t = [l.metrics.asked ? "ставка по запросу" : l.metrics.money !== "—" && l.metrics.money, l.metrics.pay].filter(Boolean).join(", ")
  return t ? <div className="text-left text-xs text-muted-foreground">{t}</div> : null
}

function RateForm({ load, profile, onRated }: { load: Load; profile: Profile; onRated: (v: { id: string; price: number; money: number; res: Pick<Load, "score" | "verdict" | "reasons" | "metrics"> }) => void }) {
  const [price, setPrice] = useState("")
  const [money, setMoney] = useState(({ "без НДС": 2, "наличными": 3, "на карту": 23 } as Record<string, number>)[load.metrics.money] ?? 1)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    try {
      onRated({ id: load.id, price: Number(price), money, res: await api("/search/rate", { body: { load: load.raw, profile, route: load.route ?? 0, price: Number(price), money } }) })
    } catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  return (
    <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (Number(price) > 0 && !busy) void submit() }}>
      <div className="flex flex-col gap-1">
        <Input aria-label="Ставка с АТИ, ₽" className="h-8 w-28" inputMode="numeric" placeholder="Ставка, ₽" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, "").slice(0, 8))} />
        <select aria-label="Форма оплаты" className="h-7 w-28 rounded-md border bg-background px-2 text-xs" value={money} onChange={(e) => setMoney(Number(e.target.value))}><option value={1}>с НДС</option><option value={2}>без НДС</option><option value={3}>наличными</option><option value={23}>на карту</option></select>
      </div>
      <Button type="submit" size="sm" disabled={busy || !(Number(price) > 0)}>{busy ? <Loader2 className="animate-spin" /> : "Оценить"}</Button>
    </form>
  )
}

function RejectDialog({ load, onClose, onDone }: { load: Load | null; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("")
  const [learn, setLearn] = useState(true)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!load || busy) return
    setBusy(true)
    try {
      const res = await api<{ rule: { text: string } | null; error?: string }>(`/loads/${load.id}/reject`, { body: { reason, learn } })
      if (res.error) toast.warning("Груз убран", { description: res.error })
      else toast.success("Груз убран", { description: res.rule ? `В базу знаний: «${res.rule.text}»` : undefined })
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
  const { data: st } = usePoll<{ default_goal: string; default_goal_text?: string; goal_presets?: string[]; send_enabled?: boolean; call_enabled?: boolean }>("/settings", 60000)
  const [goal, setGoal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [forLoad, setForLoad] = useState(load?.id)
  if (load?.id !== forLoad) { setForLoad(load?.id); setGoal(null) }  // другой груз — сценарий по умолчанию, не прошлый
  const text = goal ?? st?.default_goal_text ?? st?.default_goal ?? ""
  const contact = load?.raw.firm?.contacts?.[0]
  const submit = async () => {
    if (!load || busy || !text.trim()) return
    setBusy(true)
    try {
      const d = await api<{ chat_id: string | null; summary: string }>(`/loads/${load.id}/agent`, { body: { goal: text } })
      if (!d.chat_id)  // чат в АТИ не открылся — честно говорим, что будет дальше
        toast.warning(load.raw.manual ? "Переговоры по телефону" : "Чат с заказчиком не открылся", { description: `${d.summary}. ${st?.call_enabled ? "Агент позвонит." : "Звонки выключены — позвоните сами или включите звонки."}` })
      else
        toast.success(st?.send_enabled === false ? "Агент готовит черновик" : "Агент взял груз в работу", { description: "Переписка — во вкладке «Переговоры»" })
      setGoal(null); onClose(); onDone()
    } catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Dialog open={!!load} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Поручить переговоры агенту</DialogTitle>
          <DialogDescription>
            {load?.raw.manual ? "Груз внесён вручную, чата АТИ нет. " : ""}Агент {load?.raw.manual ? "позвонит" : "напишет"} {contact ? <b className="text-foreground">{contact.name}</b> : "заказчику"} {load?.raw.manual ? "" : "в мессенджер АТИ от вашего имени "}и уточнит то, что нужно по сценарию. {load?.raw.manual ? "Звонок — когда звонки включены, или кнопкой в «Переговорах»." : "Если не ответят в чате — позвонит (когда звонки включены)."} Сам ничего не подтверждает: позовёт вас, когда всё будет известно.
          </DialogDescription>
        </DialogHeader>
        {st && st.send_enabled === false && <div role="status" className="rounded-md bg-amber-50 p-2.5 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">Отправка сообщений выключена в настройках: агент подготовит черновик, заказчику ничего не уйдёт, пока вы не нажмёте «Отправить».</div>}
        <Label htmlFor="goal">Сценарий: что узнать</Label>
        <Textarea id="goal" rows={4} value={text} onChange={(e) => setGoal(e.target.value)} />
        <div className="flex flex-wrap gap-1.5">
          {(st?.goal_presets ?? []).map((s) => (
            <button key={s} onClick={() => setGoal(s)} className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted">{s}</button>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Отмена</Button>
          <Button onClick={submit} disabled={busy || !text.trim()}>{busy ? <Loader2 className="animate-spin" /> : <Bot />}Начать переписку</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
