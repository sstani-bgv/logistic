import { useState } from "react"
import { toast } from "sonner"
import { ArrowRight, Bot, CheckCircle2, Loader2, Send, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { api, usePoll, rub, ago, ddmm, type Dialog, type Truck } from "@/lib/api"
import { cn } from "@/lib/utils"

const COLS = [
  { id: "writing", label: "Агент пишет", tone: "bg-primary" },
  { id: "waiting", label: "Ждём ответа", tone: "bg-amber-500" },
  { id: "ready", label: "Готово — звонить", tone: "bg-emerald-500" },
  { id: "taken", label: "Взяли", tone: "bg-emerald-700" },
  { id: "declined", label: "Отказ", tone: "bg-muted-foreground" },
]
const FACTS: Record<string, string> = { actual: "Актуальность", price: "Ставка", payment: "Оплата", address: "Адрес", time: "Время погрузки", other: "Прочее" }

export default function DialogsPage() {
  const { data, reload } = usePoll<Dialog[]>("/dialogs", 4000)
  const [openId, setOpenId] = useState<number | null>(null)
  const open = data?.find((d) => d.id === openId) ?? null
  const [running, setRunning] = useState(false)
  const run = async () => {
    setRunning(true)
    try {
      const r = await api<{ started: number }>("/agent/run", { method: "POST" })
      toast.success(r.started ? `Агент взял в работу грузов: ${r.started}` : "Новых подходящих грузов нет — агент ждёт следующего прохода")
      reload()
    } catch (e) { toast.error(String(e)) } finally { setRunning(false) }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Переговоры</h1>
          <p className="text-sm text-muted-foreground">Агент сам берёт лучшие грузы, переписывается с заказчиками в мессенджере АТИ и узнаёт то, чего нет в объявлении.</p>
        </div>
        <Button onClick={run} disabled={running}>{running ? <Loader2 className="animate-spin" /> : <Bot />}Взять лучшие грузы сейчас</Button>
      </div>
      {data && data.length === 0 && <div className="rounded-xl border border-dashed bg-card p-10 text-center text-sm text-muted-foreground">Переговоров пока нет. Агент возьмёт лучшие грузы после следующего прохода по бирже.</div>}
      <div className="grid gap-4 overflow-x-auto pb-2 lg:grid-cols-5">
        {COLS.map((c) => {
          const items = data?.filter((d) => d.status === c.id) ?? []
          return (
            <div key={c.id} className="flex min-w-56 flex-col gap-2.5 rounded-xl bg-muted/60 p-2.5">
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
                    <div className="mt-2 line-clamp-3 text-xs">{d.status === "writing" ? <span className="inline-flex items-center gap-1 text-primary"><Loader2 className="size-3 animate-spin" />{d.summary}</span> : d.summary}</div>
                    {last && <div className="mt-2 border-t pt-2 text-xs text-muted-foreground"><span className="font-medium">{last.mine ? "Агент" : "Заказчик"}:</span> <span className="line-clamp-2">{last.text}</span></div>}
                    {d.facts?.price && <div className="mt-2 text-xs font-medium text-emerald-700">Ставка: {d.facts.price}</div>}
                  </button>
                )
              })}
            </div>
          )
        })}
      </div>
      <ChatSheet d={open} onClose={() => setOpenId(null)} onChange={reload} />
    </div>
  )
}

function ChatSheet({ d, onClose, onChange }: { d: Dialog | null; onClose: () => void; onChange: () => void }) {
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const r = d?.load?.raw
  const patch = async (body: object) => {
    if (!d) return
    const r = await api<{ truck?: Truck | null }>(`/dialogs/${d.id}`, { method: "PATCH", body })
    if (r.truck) toast.success(`${r.truck.name} занята до ${ddmm(r.truck.free_from)}, освободится в ${r.truck.city_name}`)
    onChange()
  }
  const send = async () => {
    if (!d || !text.trim()) return
    setBusy(true)
    try { await api(`/dialogs/${d.id}/send`, { body: { text } }); setText(""); onChange() }
    catch (e) { toast.error(String(e)) } finally { setBusy(false) }
  }
  return (
    <Sheet open={!!d} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
        {d && r && <>
          <SheetHeader className="border-b p-5">
            <SheetTitle className="flex items-center gap-1.5">{r.loading.location.city}<ArrowRight className="size-4 text-muted-foreground" />{r.unloading.location.city}</SheetTitle>
            <SheetDescription>{r.firm?.name} · {r.firm?.contacts?.[0]?.name} · {d.load?.metrics.gross ? rub(d.load.metrics.gross) : "ставка по запросу"} · #{r.loadNumber}</SheetDescription>
            <div className="mt-2 rounded-md bg-accent/70 p-2.5 text-xs"><span className="font-medium">Цель агента:</span> {d.goal}</div>
          </SheetHeader>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 border-b bg-muted/30 p-5 text-sm">
            {Object.entries(FACTS).map(([k, label]) => (
              <div key={k} className="min-w-0"><div className="text-xs text-muted-foreground">{label}</div><div className={cn("truncate", !d.facts?.[k] && "text-muted-foreground")}>{d.facts?.[k] ?? "—"}</div></div>
            ))}
          </div>
          <div className="flex flex-1 flex-col gap-2.5 overflow-y-auto p-5">
            {d.messages.length === 0 && <div className="m-auto text-sm text-muted-foreground">Агент готовит первое сообщение…</div>}
            {d.messages.map((m) => (
              <div key={m.id} className={cn("max-w-[85%] rounded-2xl px-3.5 py-2 text-sm", m.mine ? "self-end rounded-br-sm bg-primary text-primary-foreground" : "self-start rounded-bl-sm border bg-card")}>
                {!!m.mine && <div className="mb-0.5 flex items-center gap-1 text-[11px] opacity-75"><Bot className="size-3" />агент</div>}
                <div className="whitespace-pre-wrap">{m.text}</div>
                <div className="mt-1 text-right text-[11px] opacity-60">{ago(m.ts)}</div>
              </div>
            ))}
          </div>
          <div className="flex flex-col gap-3 border-t p-4">
            <div className="flex gap-2">
              <Input id="manual-msg" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} placeholder="Написать вручную…" />
              <Button size="icon" onClick={send} disabled={busy}><Send /></Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Switch id="auto" checked={!!d.auto} onCheckedChange={(v) => patch({ auto: v })} /><Label htmlFor="auto" className="text-sm">Агент отвечает сам</Label>
              <div className="ml-auto flex gap-2">
                <Button size="sm" variant="outline" onClick={() => patch({ status: "declined" })}><XCircle />Отказ</Button>
                <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700" onClick={() => patch({ status: "taken" })}><CheckCircle2 />Берём</Button>
              </div>
            </div>
          </div>
        </>}
      </SheetContent>
    </Sheet>
  )
}
