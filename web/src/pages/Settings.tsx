import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Save } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api, errText, type Settings } from "@/lib/api"

type Field = { k: string; label: string; hint?: string; type: "number" | "bool" | "text" | "long" }
const GROUPS: { title: string; fields: Field[] }[] = [
  { title: "Отбор грузов", fields: [
    { k: "min_rate_km", label: "Минимум ₽/км чистыми", type: "number" },
    { k: "threshold", label: "Порог оценки «подходит»", hint: "0–100", type: "number" },
    { k: "vat", label: "НДС, %", type: "number" },
    { k: "scan_minutes", label: "Проход по бирже, мин", type: "number" },
    { k: "pages", label: "Страниц выдачи на машину", type: "number" },
  ] },
  { title: "Агент и переписка", fields: [
    { k: "send_enabled", label: "Агент отправляет сообщения", hint: "выкл — только черновики", type: "bool" },
    { k: "auto_start", label: "Агент сам берёт лучшие грузы", type: "bool" },
    { k: "auto_score", label: "С какой оценки берёт сам", type: "number" },
    { k: "auto_per_cycle", label: "Новых диалогов за проход", type: "number" },
    { k: "auto_daily", label: "Новых диалогов в день", type: "number" },
    { k: "default_goal", label: "Цель переговоров по умолчанию", type: "long" },
  ] },
  { title: "Звонки", fields: [
    { k: "call_enabled", label: "Агент звонит", type: "bool" },
    { k: "call_after_min", label: "Звонить, если нет ответа в чате, мин", type: "number" },
    { k: "call_hours", label: "Окно звонков (МСК)", hint: "например 09:00-19:00", type: "text" },
    { k: "call_daily", label: "Звонков в день, максимум", type: "number" },
  ] },
]

export default function SettingsPage() {
  const [s, setS] = useState<Settings | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api<Settings>("/settings").then(setS).catch((e) => toast.error(errText(e))) }, [])
  const set = (k: string, v: unknown) => setS((p) => (p ? { ...p, [k]: v } : p))
  const save = async () => {
    if (!s) return
    setBusy(true)
    try { setS(await api<Settings>("/settings", { method: "PUT", body: s })); toast.success("Настройки сохранены") }
    catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  if (!s) return <div className="text-sm text-muted-foreground">Загрузка…</div>
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Настройки</h1>
          <p className="text-sm text-muted-foreground">Лимиты агента, отбор грузов и звонки. Видит только владелец.</p>
        </div>
        <Button onClick={save} disabled={busy}><Save />Сохранить</Button>
      </div>
      {GROUPS.map((g) => (
        <section key={g.title} className="rounded-xl border bg-card p-5">
          <h2 className="mb-4 font-medium">{g.title}</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {g.fields.filter((f) => f.k in s).map((f) => {
              const id = `s-${f.k}`
              if (f.type === "bool") return (
                <div key={f.k} className="flex items-center gap-2 sm:col-span-2">
                  <Switch id={id} checked={!!s[f.k]} onCheckedChange={(v) => set(f.k, v)} />
                  <Label htmlFor={id}>{f.label}</Label>
                  {f.hint && <span className="text-xs text-muted-foreground">{f.hint}</span>}
                </div>
              )
              if (f.type === "long") return (
                <div key={f.k} className="flex flex-col gap-1.5 sm:col-span-2">
                  <Label htmlFor={id}>{f.label}</Label>
                  <Textarea id={id} rows={4} value={String(s[f.k] ?? "")} onChange={(e) => set(f.k, e.target.value)} />
                </div>
              )
              return (
                <div key={f.k} className="flex flex-col gap-1.5">
                  <Label htmlFor={id}>{f.label}</Label>
                  <Input id={id} type={f.type} value={String(s[f.k] ?? "")} placeholder={f.hint}
                    onChange={(e) => set(f.k, f.type === "number" ? Number(e.target.value) : e.target.value)} />
                </div>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}
