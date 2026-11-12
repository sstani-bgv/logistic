import { useState } from "react"
import { toast } from "sonner"
import { ArrowRight, Ban, Bot, CheckCircle2, Loader2, Phone, PhoneCall, Send, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { api, ask, usePoll, rub, ago, ddmm, errText, type Call, type Dialog, type DialogPatchResult } from "@/lib/api"
import { cn } from "@/lib/utils"
import VoiceTest from "@/components/VoiceTest"

const COLS = [
  { id: "writing", label: "Агент пишет", tone: "bg-primary" },
  { id: "waiting", label: "Ждём ответа", tone: "bg-amber-500" },
  { id: "ready", label: "Готово — звонить", tone: "bg-emerald-500" },
  { id: "taken", label: "Взяли", tone: "bg-emerald-700" },
  { id: "declined", label: "Отказ", tone: "bg-muted-foreground" },
]
const FACTS: Record<string, string> = { actual: "Актуальность", price: "Ставка", payment: "Оплата", address: "Адрес", time: "Время погрузки", other: "Прочее" }

export default function DialogsPage() {
  const { data, reload, offline } = usePoll<Dialog[]>("/dialogs", 4000)
  const { data: st } = usePoll<{ voice_ready?: boolean }>("/settings", 60000)
  const [openId, setOpenId] = useState<number | null>(null)
  const open = data?.find((d) => d.id === openId) ?? null
  const [running, setRunning] = useState(false)
  const run = async () => {
    if (!ask("Агент сам выберет лучшие свежие грузы и напишет заказчикам в мессенджер АТИ. Продолжить?")) return
    setRunning(true)
    try {
      const r = await api<{ started: number; reason?: string | null }>("/agent/run", { method: "POST" })
      if (r.started) toast.success(`Агент взял в работу грузов: ${r.started}`)
      else toast.info(r.reason ?? "Новых подходящих грузов нет")
      reload()
    } catch (e) { toast.error(errText(e)) } finally { setRunning(false) }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Переговоры</h1>
          <p className="text-sm text-muted-foreground">Агент переписывается с заказчиками в мессенджере АТИ и узнаёт то, чего нет в объявлении. Если в чате молчат — звонит (когда звонки включены). Ничего не подтверждает: решение за вами.</p>
        </div>
        <Button onClick={run} disabled={running}>{running ? <Loader2 className="animate-spin" /> : <Bot />}Взять лучшие грузы сейчас</Button>
      </div>
      {offline && <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">Нет связи с сервером — показаны последние полученные данные.</div>}
      {data && data.length === 0 && <div className="rounded-xl border border-dashed bg-card p-10 text-center text-sm text-muted-foreground">Переговоров пока нет. Поручите агенту груз на странице «Грузы» — или, когда владелец включит автостарт, агент сам возьмёт лучшие.</div>}
      <div className="grid auto-cols-[minmax(15rem,1fr)] grid-flow-col gap-4 overflow-x-auto pb-2">
        {COLS.map((c) => {
          const items = data?.filter((d) => d.status === c.id) ?? []
          return (
            <div key={c.id} className="flex min-w-0 flex-col gap-2.5 rounded-xl bg-muted/60 p-2.5">
              <div className="flex items-center gap-2 px-1 text-sm font-medium">
                <span className={cn("size-2 rounded-full", c.tone)} />{c.label}<span className="num ml-auto text-muted-foreground">{items.length}</span>
              </div>
              {items.map((d) => {
                const r = d.load?.raw
                const last = d.messages.at(-1)
                return (
                  <button key={d.id} onClick={() => setOpenId(d.id)} className="rounded-lg border bg-card p-3 text-left text-sm shadow-xs transition-shadow hover:shadow-sm">
                    {r && <div className="flex items-center gap-1 font-medium">{r.loading.location.city}<ArrowRight className="size-3.5 text-muted-foreground" />{r.unloading.location.city}</div>}
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">{r?.firm?.name}</div>
                    <div className={cn("mt-2 line-clamp-3 text-xs", d.summary?.startsWith("Ошибка") && "font-medium text-destructive")}>{d.status === "writing" ? <span className="inline-flex items-center gap-1 text-primary"><Loader2 className="size-3 animate-spin" />{d.summary}</span> : d.summary}</div>
                    {last && <div className="mt-2 border-t pt-2 text-xs text-muted-foreground"><span className="font-medium">{last.mine ? "Агент" : "Заказчик"}:</span> <span className="line-clamp-2">{last.text}</span></div>}
                    {d.facts?.price && <div className="mt-2 text-xs font-medium text-emerald-700">Ставка: {d.facts.price}</div>}
                  </button>
                )
              })}
            </div>
          )
        })}
      </div>
      <ChatSheet key={openId ?? 0} d={open} voice={!!st?.voice_ready} onClose={() => setOpenId(null)} onChange={reload} />
    </div>
  )
}

function ChatSheet({ d, voice, onClose, onChange }: { d: Dialog | null; voice: boolean; onClose: () => void; onChange: () => void }) {
  const [text, setText] = useState("")
  const [goalOpen, setGoalOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const r = d?.load?.raw
  const [saving, setSaving] = useState(false)
  const patch = async (body: Partial<{ auto: boolean; status: "taken" | "declined" }>) => {
    if (!d || saving) return
    setSaving(true)
    try {
      const res = await api<DialogPatchResult>(`/dialogs/${d.id}`, { method: "PATCH", body })
      if (body.status === "taken" && res.truck)
        toast.success(`${res.truck.name} занята до ${ddmm(res.truck.free_from)}, освободится в ${res.truck.city_name}`)
      onChange()
    } catch (e) { toast.error(errText(e)) } finally { setSaving(false) }
  }
  const [calling, setCalling] = useState(false)
  const callNow = async () => {
    if (!d || !ask(`Позвонить ${d.phone ?? "заказчику"} сейчас? Звонок уйдёт сразу, даже если автозвонки выключены.`)) return
    setCalling(true)
    try { const c = await api<Call>(`/dialogs/${d.id}/call`, { method: "POST" }); toast.success(`Звоним ${c.phone}`); onChange() }
    catch (e) { toast.error(errText(e)) } finally { setCalling(false) }
  }
  const noCalls = async () => {
    if (!d?.phone || !ask(`Больше никогда не звонить на ${d.phone}?`)) return
    try { await api("/dnc", { body: { phone: d.phone, reason: "логист: не звонить" } }); toast.success(`${d.phone} — в стоп-листе`) }
    catch (e) { toast.error(errText(e)) }
  }
  const send = async (body = text) => {
    if (!d || busy || !body.trim()) return
    setBusy(true)
    try { await api(`/dialogs/${d.id}/send`, { body: { text: body } }); setText(""); onChange() }
    catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Sheet open={!!d} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
        {d && r && <>
          <div className="min-h-0 flex-1 overflow-y-auto">
          <SheetHeader className="border-b p-5">
            <SheetTitle className="flex items-center gap-1.5">{r.loading.location.city}<ArrowRight className="size-4 text-muted-foreground" />{r.unloading.location.city}</SheetTitle>
            <SheetDescription>{r.firm?.name} · {r.firm?.contacts?.[0]?.name} · {d.load?.metrics.gross ? rub(d.load.metrics.gross) : "ставка по запросу"} · #{r.loadNumber}</SheetDescription>
            <button type="button" onClick={() => setGoalOpen((v) => !v)} title={goalOpen ? "Свернуть" : "Показать сценарий целиком"}
              className={cn("mt-2 rounded-md bg-accent/70 p-2.5 text-left text-xs", !goalOpen && "line-clamp-2")}>
              <span className="font-medium">Сценарий агента:</span> {d.goal}
            </button>
          </SheetHeader>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 border-b bg-muted/30 p-5 text-sm">
            {Object.entries(FACTS).map(([k, label]) => (
              <div key={k} className="min-w-0"><div className="text-xs text-muted-foreground">{label}</div><div title={d.facts?.[k] ?? undefined} className={cn("line-clamp-3 break-words", !d.facts?.[k] && "text-muted-foreground")}>{d.facts?.[k] ?? "—"}</div></div>
            ))}
          </div>
          <div className="flex flex-col gap-2.5 p-5">
            {d.messages.length === 0 && <div className="m-auto text-sm text-muted-foreground">{d.chat_id ? "Агент готовит первое сообщение…" : "Чат не открылся — агент позвонит по телефону"}</div>}
            {d.messages.map((m) => m.kind === "call" ? (
              <div key={m.id} className="w-full rounded-xl border border-sky-200 bg-sky-50 px-3.5 py-2 text-sm dark:border-sky-900 dark:bg-sky-950/40">
                <div className="mb-1 flex items-center gap-1 text-[11px] text-muted-foreground"><PhoneCall className="size-3" />расшифровка звонка</div>
                <div className="whitespace-pre-wrap">{m.text}</div>
              </div>
            ) : (
              <div key={m.id} className={cn("max-w-[85%] rounded-2xl px-3.5 py-2 text-sm", m.mine ? "self-end rounded-br-sm bg-primary text-primary-foreground" : "self-start rounded-bl-sm border bg-card")}>
                {!!m.mine && <div className="mb-0.5 flex items-center gap-1 text-[11px] opacity-75"><Bot className="size-3" />агент</div>}
                <div className="whitespace-pre-wrap">{m.text}</div>
                <div className="mt-1 text-right text-[11px] opacity-60">{ago(m.ts)}</div>
              </div>
            ))}
          </div>
          {!!d.calls?.length && (
            <div className="flex flex-col gap-2 border-t p-4">
              {d.calls.map((c) => <CallRow key={c.id} c={c} />)}
            </div>
          )}
          </div>
          {d.draft && (
            <div className="flex flex-col gap-2 border-t bg-amber-50/60 p-4 dark:bg-amber-950/20">
              <div className="text-xs font-medium text-amber-800 dark:text-amber-300">Черновик агента — заказчик его ещё не видит</div>
              <div className="whitespace-pre-wrap text-sm">{d.draft}</div>
              <div className="flex gap-2">
                <Button size="sm" disabled={busy || !d.chat_id} onClick={() => ask("Отправить черновик заказчику в мессенджер АТИ?") && send(d.draft ?? "")}><Send />Отправить</Button>
                <Button size="sm" variant="outline" onClick={() => setText(d.draft ?? "")}>Поправить</Button>
              </div>
            </div>
          )}
          <div className="flex flex-col gap-3 border-t p-4">
            {voice && <VoiceTest dialogId={d.id} />}
            <div className="flex gap-2">
              <Input id="manual-msg" aria-label="Сообщение заказчику" value={text} disabled={!d.chat_id} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} placeholder={d.chat_id ? "Написать вручную…" : "Чата с заказчиком нет — только звонок"} />
              <Button size="icon" onClick={() => send()} disabled={busy || !d.chat_id || !text.trim()} aria-label="Отправить"><Send /></Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Switch id="auto" checked={!!d.auto} disabled={saving} onCheckedChange={(v) => patch({ auto: v })} /><Label htmlFor="auto" className="text-sm">Агент отвечает сам</Label>
              <div className="ml-auto flex flex-wrap gap-2">
                <Button size="sm" variant="outline" disabled={calling || !d.phone} title={d.phone ? `Позвонить ${d.phone}` : "У заказчика нет телефона в АТИ"} onClick={callNow}>{calling ? <Loader2 className="animate-spin" /> : <Phone />}Позвонить сейчас</Button>
                {d.phone && <Button size="sm" variant="ghost" onClick={noCalls} title="Больше не звонить на этот номер"><Ban />Не звонить</Button>}
                <Button size="sm" variant="outline" disabled={saving} onClick={() => ask("Отказаться от груза? Переговоры закроются.") && patch({ status: "declined" })}><XCircle />Отказ</Button>
                <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700" disabled={saving} onClick={() => ask("Берём груз? Машина станет занятой до выгрузки.") && patch({ status: "taken" })}><CheckCircle2 />Берём</Button>
              </div>
            </div>
          </div>
        </>}
      </SheetContent>
    </Sheet>
  )
}

const CALL_STATUS: Record<Call["status"], string> = {
  queued: "в очереди", ringing: "дозваниваемся", talking: "идёт разговор", done: "состоялся",
  no_answer: "не ответили", busy: "занято", failed: "ошибка",
}

function CallRow({ c }: { c: Call }) {
  const m = Math.floor((c.duration || 0) / 60), s = (c.duration || 0) % 60
  return (
    <div className="rounded-lg border bg-muted/30 p-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <PhoneCall className="size-3.5 text-muted-foreground" />
        <span className="font-medium">{c.phone}</span>
        <span className={cn("text-xs", c.status === "failed" ? "text-destructive" : "text-muted-foreground")}>{CALL_STATUS[c.status] ?? c.status}</span>
        {c.status === "done" && <span className="num text-xs text-muted-foreground">{m}:{String(s).padStart(2, "0")} · {c.cost} ₽</span>}
        <span className="ml-auto text-xs text-muted-foreground">{c.by_login && c.by_login !== "агент" ? c.by_login : "агент"} · {ago(c.created)}</span>
      </div>
      {c.error && <div className="mt-1 text-xs text-destructive">{c.error}</div>}
      {c.recording_url?.startsWith("/api/") && <audio controls preload="none" src={c.recording_url} className="mt-2 h-8 w-full" aria-label={`Запись звонка ${c.phone}`} />}
    </div>
  )
}
