import { useState } from "react"
import { toast } from "sonner"
import { Ban, Brain, Loader2, Trash2, TrendingDown, TrendingUp, StickyNote } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api, errText, usePoll, ago, type Rule } from "@/lib/api"
import { cn } from "@/lib/utils"

const MATCH: Record<string, string> = { firm_id: "фирма", firm_name: "фирма", cargo: "тип груза", load_city: "погрузка", unload_city: "выгрузка", note: "примечание", profile: "тип заказчика", none: "заметка агенту" }

export default function KnowledgePage() {
  const { data: rules, reload } = usePoll<Rule[]>("/rules", 8000)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const add = async () => {
    setBusy(true)
    try { const r = await api<Rule>("/rules", { body: { text } }); toast.success("Правило добавлено", { description: r.text }); setText(""); reload() }
    catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  const toggle = async (r: Rule, active: boolean) => { await api(`/rules/${r.id}`, { method: "PATCH", body: { active } }); reload() }
  const del = async (r: Rule) => { await api(`/rules/${r.id}`, { method: "DELETE" }); reload() }

  return (
    <div className="flex flex-col gap-6">
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
    </div>
  )
}
