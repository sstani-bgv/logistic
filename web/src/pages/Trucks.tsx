import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Loader2, Pencil, Plus, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { CityInput } from "@/components/CityInput"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { api, num, usePoll, ddmm, errText, BODIES, bodiesIn, bodyMask, DISTRICTS, type Truck } from "@/lib/api"

const EMPTY = { id: 0, name: "", driver: "", city_name: "", radius: 150, body_mask: 1, body_name: "тент", height: 2.7, capacity: 20, free_from: new Date().toISOString().slice(0, 10), active: 1, relevant: 0, status: "free" as const, to_city_name: "", to_radius: 150, to_district: "" }
const where = (t: Truck) => t.to_district ? t.to_district : t.to_city_name ? `${t.to_city_name} ±${t.to_radius ?? 150}` : "вся Россия"

export default function TrucksPage() {
  const { data, reload } = usePoll<Truck[]>("/trucks", 8000)
  const [edit, setEdit] = useState<Truck | null>(null)
  const [scanning, setScanning] = useState<number | null>(null)
  const [toggling, setToggling] = useState<number | null>(null)
  // Сразу показываем выбор, а после ответа — то, что сервер записал (дату тоже), пока поллинг не догонит
  const [pending, setPending] = useState<Record<number, Partial<Truck>>>({})
  const toggle = async (t: Truck, free: boolean) => {
    const status = free ? "free" : "busy"
    setToggling(t.id); setPending((p) => ({ ...p, [t.id]: { status } }))
    try {
      const r = await api<Truck>(`/trucks/${t.id}`, { method: "PATCH", body: { status } })
      setPending((p) => ({ ...p, [t.id]: { status: r.status, free_from: r.free_from } }))
      toast.success(free ? `${t.name} свободна — ищем грузы` : `${t.name} занята до ${ddmm(r.free_from)} — поправьте дату и город, если нужно`)
      await reload()
    } catch (e) {
      toast.error(errText(e))
      setPending((p) => { const n = { ...p }; delete n[t.id]; return n })
    } finally { setToggling(null) }
  }
  useEffect(() => {  // поллинг догнал записанное — подмена больше не нужна
    setPending((p) => {
      const n = { ...p }
      for (const tr of data ?? []) if (n[tr.id] && n[tr.id].status === tr.status && (!n[tr.id].free_from || n[tr.id].free_from === tr.free_from)) delete n[tr.id]
      return Object.keys(n).length === Object.keys(p).length ? p : n
    })
  }, [data])
  const scan = async (t: Truck) => {
    setScanning(t.id)
    try { await api(`/trucks/${t.id}/scan`, { method: "POST" }); toast.success(`Биржа просмотрена для ${t.name}`); reload() }
    catch (e) { toast.error(errText(e)) } finally { setScanning(null) }
  }
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Машины</h1>
          <p className="text-sm text-muted-foreground">Где и когда освобождается машина — под это ассистент и ищет грузы.</p>
        </div>
        <Button onClick={() => setEdit({ ...EMPTY })}><Plus />Добавить машину</Button>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data?.map((tr) => { const t = pending[tr.id] ? { ...tr, ...pending[tr.id] } : tr; return (
          <div key={t.id} className="flex flex-col gap-3 rounded-xl border bg-card p-4">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-semibold">{t.name}</div>
                <div className="text-sm text-muted-foreground">{t.driver && `${t.driver} · `}{t.body_name}, {t.capacity} т, высота {t.height} м</div>
              </div>
              {!t.active ? <Badge variant="outline">на паузе</Badge>
                : t.status === "busy" ? <Badge variant="outline">занята</Badge> : <Badge variant="secondary">ищем груз</Badge>}
            </div>
            <div className="flex items-center gap-2">
              <Switch id={`free-${t.id}`} aria-label="Машина свободна" checked={t.status !== "busy"} disabled={toggling === t.id} onCheckedChange={(v) => toggle(t, v)} />
              <Label htmlFor={`free-${t.id}`} className="text-sm">Свободна</Label>
              {t.status === "busy" && <span className="text-xs text-muted-foreground">занята до {ddmm(t.free_from)}</span>}
            </div>
            <div className="grid grid-cols-3 gap-2 rounded-lg bg-muted/50 p-3 text-sm">
              <div><div className="text-xs text-muted-foreground">{t.status === "busy" ? "Освободится в" : "Свободна в"}</div><div className="font-medium">{t.city_name}</div></div>
              <div><div className="text-xs text-muted-foreground">С даты</div><div className="font-medium">{t.free_from && t.free_from > new Date().toLocaleDateString("sv-SE") ? ddmm(t.free_from) : "сейчас"}</div></div>
              <div><div className="text-xs text-muted-foreground">Радиус</div><div className="num font-medium">{t.radius} км</div></div>
              <div className="col-span-3"><span className="text-xs text-muted-foreground">Куда: </span><span className="font-medium">{where(t)}</span></div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm"><span className="num font-semibold text-primary">{t.relevant}</span> <span className="text-muted-foreground">подходящих грузов</span></span>
              <div className="flex gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => setEdit(t)}><Pencil /></Button>
                <Button size="sm" variant="outline" onClick={() => scan(t)} disabled={scanning === t.id}>{scanning === t.id ? <Loader2 className="animate-spin" /> : <RefreshCw />}Искать сейчас</Button>
              </div>
            </div>
          </div>
        )})}
      </div>
      <TruckDialog t={edit} onClose={() => setEdit(null)} onDone={reload} />
    </div>
  )
}

function TruckDialog({ t, onClose, onDone }: { t: Truck | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<Truck | null>(null)
  const [busy, setBusy] = useState(false)
  const [dest, setDest] = useState<"any" | "city" | "district" | null>(null)  // выбор «куда», пока не сохранили
  const cur = f?.id === t?.id && f ? f : t
  const mode = dest ?? (cur?.to_district ? "district" : cur?.to_city_name ? "city" : "any")
  const close = () => { setF(null); setDest(null); onClose() }
  const set = (k: keyof Truck, v: string | number | null) => setF({ ...(cur as Truck), [k]: v })
  const bad = !cur || !String(cur.name ?? "").trim() || !String(cur.city_name ?? "").trim() || !cur.free_from ||
    [cur.radius, cur.height, cur.capacity].some((x) => x === null || x === undefined || !(Number(x) > 0)) ||
    [cur.radius, cur.to_radius ?? 150].some((x) => Number(x) > 999) ||
    (mode === "city" && (!String(cur.to_city_name ?? "").trim() || !(Number(cur.to_radius) > 0)))
  const save = async () => {
    if (!cur || busy || bad) return
    setBusy(true)
    try {
      const to = { to_city_name: mode === "city" ? cur.to_city_name ?? "" : "", to_district: mode === "district" ? cur.to_district || "ЦФО" : "", to_radius: cur.to_radius ?? 150 }
      await api(cur.id ? `/trucks/${cur.id}` : "/trucks", { method: cur.id ? "PUT" : "POST", body: { ...cur, ...to, active: !!cur.active } })
      toast.success("Сохранено — оценки грузов пересчитаны"); close(); onDone()
    } catch (e) { toast.error(errText(e)) } finally { setBusy(false) }
  }
  const field = (k: keyof Truck, label: string, type = "text") => (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`t-${k}`}>{label}</Label>
      {type === "city"
        ? <CityInput id={`t-${k}`} value={String(cur?.[k] ?? "")} onChange={(v) => set(k, v)} />
        : <Input id={`t-${k}`} type={type} value={String(cur?.[k] ?? "")} onChange={(e) => set(k, type === "number" ? num(e.target.value) : e.target.value)} />}
    </div>
  )
  return (
    <Dialog open={!!t} onOpenChange={(o) => { if (!o) close() }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{cur?.id ? "Машина" : "Новая машина"}</DialogTitle></DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          {field("name", "Машина и госномер")}{field("driver", "Водитель")}
          {field("city_name", "Где освобождается", "city")}{field("free_from", "С какой даты (в будущем — занята)", "date")}
          {field("radius", "Радиус поиска, км (до 999)", "number")}{field("height", "Высота кузова, м", "number")}
          {field("capacity", "Грузоподъёмность, т", "number")}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="t-body">Кузов</Label>
            <BodyPicker names={String(cur?.body_name ?? "")} onChange={(mask, name) => setF({ ...(cur as Truck), body_mask: mask, body_name: name })} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="t-dest">Куда нужно машине</Label>
            <select id="t-dest" className="h-9 rounded-md border bg-background px-3 text-sm" value={mode} onChange={(e) => {
              const m = e.target.value as typeof mode
              setDest(m); if (m === "district" && !cur?.to_district) set("to_district", "ЦФО")
            }}>
              <option value="any">Вся Россия — любое направление</option><option value="city">В город</option><option value="district">В федеральный округ</option>
            </select>
          </div>
          {mode === "district" && <div className="flex flex-col gap-1.5"><Label htmlFor="t-district">Округ</Label>
            <select id="t-district" className="h-9 rounded-md border bg-background px-3 text-sm" value={cur?.to_district || "ЦФО"} onChange={(e) => set("to_district", e.target.value)}>
              {Object.entries(DISTRICTS).map(([k, v]) => <option key={k} value={k}>{k} — {v}</option>)}
            </select></div>}
          {mode === "city" && field("to_city_name", "Город выгрузки", "city")}
          {mode === "city" && field("to_radius", "Радиус вокруг него, км", "number")}
        </div>
        <div className="flex items-center gap-2"><Switch id="t-active" checked={!!cur?.active} onCheckedChange={(v) => set("active", v ? 1 : 0)} /><Label htmlFor="t-active">Искать грузы</Label></div>
        <DialogFooter><Button variant="outline" onClick={close}>Отмена</Button><Button onClick={save} disabled={busy || bad}>{busy && <Loader2 className="animate-spin" />}Сохранить</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}


/** Несколько кузовов, как в АТИ: в поиск уходит сумма кодов, в карточку — названия через запятую. */
function BodyPicker({ names, onChange }: { names: string; onChange: (mask: number, name: string) => void }) {
  const picked = bodiesIn(names)
  const toggle = (m: number) => {
    const next = picked.some(([, x]) => x === m) ? picked.filter(([, x]) => x !== m) : [...picked, BODIES.find(([, x]) => x === m)!]
    if (next.length) onChange(bodyMask(next.map(([, x]) => x)), next.map(([n]) => n).join(", "))
  }
  return (
    <details id="t-body" className="relative">
      <summary className="flex h-9 cursor-pointer list-none items-center truncate rounded-md border bg-background px-3 text-sm">{picked.map(([n]) => n).join(", ") || (names ? `${names} — выберите из списка` : "выберите")}</summary>
      <div className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border bg-popover p-1 shadow-md">
        {BODIES.map(([n, m]) => (
          <label key={m} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-muted">
            <input type="checkbox" checked={picked.some(([, x]) => x === m)} onChange={() => toggle(m)} />{n}
          </label>
        ))}
      </div>
    </details>
  )
}
