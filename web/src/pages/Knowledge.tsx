import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Ban, Brain, Loader2, Trash2, TrendingDown, TrendingUp, StickyNote } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api, usePoll, ago, type Rule } from "@/lib/api"
import { cn } from "@/lib/utils"

const MATCH: Record<string, string> = { firm_id: "фирма", firm_name: "фирма", cargo: "тип груза", load_city: "погрузка", unload_city: "выгрузка", note: "примечание", profile: "тип заказчика", none: "заметка агенту" }

type Settings = {
  vat: number; min_rate_km: number; threshold: number; scan_minutes: number; send_enabled: boolean; default_goal: string
  auto_start: boolean; auto_score: number; auto_per_cycle: number; auto_daily: number
}

export default function KnowledgePage() {
  const { data: rules, reload } = usePoll<Rule[]>("/rules", 8000)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const add = async () => {
    setBusy(true)
    try { const r = await api<Rule>("/rules", { body: { text } }); toast.success("Правило добавлено", { description: r.text }); setText(""); reload() }
    catch (e) { toast.error(String(e)) } finally { setBusy(false) }
  }
  const toggle = async (r: Rule, active: boolean) => { await api(`/rules/${r.id}`, { method: "PATCH", body: { active } }); reload() }
  const del = async (r: Rule) => { await api(`/rules/${r.id}`, { method: "DELETE" }); reload() }

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
      <div className="flex min-w-0 flex-col gap-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">База знаний</h1>
          <p className="text-sm text-muted-foreground">Всё, чему ассистента научил логист. Каждый отказ с объяснением превращается в правило — и такие грузы больше не попадают в ленту.</p>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <Label htmlFor="new-rule" className="mb-2 flex items-center gap-1.5"><Brain className="size-4 text-primary" />Научить ассистента</Label>
          <Textarea id="new-rule" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Например: в Мурманск не ездим зимой; грузы от ООО «Ромашка» не брать — не платят" />
          <div className="mt-2 flex justify-end"><Button onClick={add} disabled={busy || !text.trim()}>{busy && <Loader2 className="animate-spin" />}{busy ? "Разбираю…" : "Добавить правило"}</Button></div>
        </div>
        <div className="flex flex-col gap-2">
          {rules?.length === 0 && <div className="rounded-xl border border-dashed bg-card p-8 text-center text-sm text-muted-foreground">Пока пусто. Нажмите «Не то» у любого груза и объясните почему — правило появится здесь.</div>}
          {rules?.map((r) => {
            const Icon = r.match === "none" ? StickyNote : r.block ? Ban : r.delta < 0 ? TrendingDown : TrendingUp
            return (
              <div key={r.id} className={cn("flex items-start gap-3 rounded-xl border bg-card p-4", !r.active && "opacity-55")}>
                <div className={cn("grid size-9 shrink-0 place-items-center rounded-lg", r.block ? "bg-red-50 text-red-600" : r.delta < 0 ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700")}><Icon className="size-4" /></div>
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{r.text}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {r.match !== "none" && <>если {MATCH[r.match]} содержит «{r.value}» → {r.block ? "не показывать" : `${r.delta > 0 ? "+" : ""}${r.delta} к оценке`} · </>}{ago(r.created)}
                  </div>
                  <div className="mt-1.5 text-sm text-muted-foreground">{r.source.startsWith("Созвон") ? r.source : `Логист: «${r.source}»`}</div>
                </div>
                <Switch checked={!!r.active} onCheckedChange={(v) => toggle(r, v)} aria-label="Включить правило" />
                <Button size="icon" variant="ghost" onClick={() => del(r)} aria-label="Удалить"><Trash2 /></Button>
              </div>
            )
          })}
        </div>
      </div>
      <SettingsCard />
    </div>
  )
}

function SettingsCard() {
  const { data } = usePoll<Settings>("/settings", 60000)
  const [s, setS] = useState<Settings | null>(null)
  useEffect(() => { if (data && !s) setS(data) }, [data, s])
  if (!s) return null
  const save = async () => { await api("/settings", { method: "PUT", body: s }); toast.success("Сохранено — грузы переоценены") }
  const num = (k: keyof Settings, label: string, hint: string) => (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`s-${k}`}>{label}</Label>
      <Input id={`s-${k}`} type="number" value={String(s[k])} onChange={(e) => setS({ ...s, [k]: Number(e.target.value) })} />
      <span className="text-xs text-muted-foreground">{hint}</span>
    </div>
  )
  return (
    <div className="flex h-fit flex-col gap-4 rounded-xl border bg-card p-5 xl:sticky xl:top-6">
      <div className="font-semibold">Базовые правила отбора</div>
      {num("min_rate_km", "Минимальная ставка, ₽/км", "чистыми, с учётом холостого пробега")}
      {num("vat", "НДС, %", "«с НДС» пересчитывается в чистые деньги")}
      {num("threshold", "Порог релевантности", "ниже — груз уходит в «отсеяно»")}
      {num("scan_minutes", "Проход по бирже, мин", "чаще — быстрее реакция")}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="s-goal">Цель агента по умолчанию</Label>
        <Textarea id="s-goal" rows={4} value={s.default_goal} onChange={(e) => setS({ ...s, default_goal: e.target.value })} />
      </div>
      <div className="flex items-center gap-2"><Switch id="s-send" checked={s.send_enabled} onCheckedChange={(v) => setS({ ...s, send_enabled: v })} /><Label htmlFor="s-send">Агент отправляет сообщения</Label></div>
      <div className="flex items-center gap-2"><Switch id="s-auto" checked={s.auto_start} onCheckedChange={(v) => setS({ ...s, auto_start: v })} /><Label htmlFor="s-auto">Агент сам берёт лучшие грузы</Label></div>
      {s.auto_start && <div className="grid grid-cols-3 gap-2">
        {num("auto_score", "Оценка от", "")}{num("auto_per_cycle", "За проход", "")}{num("auto_daily", "В день", "")}
      </div>}
      <Button onClick={save}>Сохранить</Button>
    </div>
  )
}
