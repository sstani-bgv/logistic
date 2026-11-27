import { useEffect, useState } from "react"
import { toast } from "sonner"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { api, errText, type Load } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { CityInput } from "@/components/CityInput"
import { Label } from "@/components/ui/label"

// Направление как строка «Откуда → Куда» в фильтре АТИ: у каждого города свой радиус, «куда» пусто — любое
export type Route = { from_city: string; from_radius: number; to_city: string; to_radius: number }
const NEW_ROUTE: Route = { from_city: "", from_radius: 150, to_city: "", to_radius: 150 }
export type Profile = {
  routes: Route[]; body_mask: number
  capacity: number; height: number; default_mode: "general" | "trucks"
  auto_refresh: boolean; refresh_hours: number
}
export type SearchResult = { loads: Load[]; total: number; page: number; more?: boolean; public_only: boolean; profile: Profile; updated_at?: string }

export default function SearchPanel({ settings = false, onResults, onDefaultMode }: {
  settings?: boolean
  onResults?: (result: SearchResult) => void
  onDefaultMode?: (mode: "general" | "trucks") => void
}) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState<number | null>(null)
  const [more, setMore] = useState(false)
  const [error, setError] = useState("")
  const [expanded, setExpanded] = useState(settings)
  const [updated, setUpdated] = useState<string>()

  const show = (result: SearchResult) => { setPage(result.page); setTotal(result.total); setMore(!!result.more); setUpdated(result.updated_at); onResults?.(result) }
  // Выдача кэшируется на сервере: листание и повторный поиск берут кэш, «Обновить грузы» идёт на биржу заново.
  async function search(value: Profile, nextPage = 1, refresh = false) {
    setBusy(true); setError("")
    try { show(await api<SearchResult>("/search", { method: "POST", body: { ...value, page: nextPage, refresh } })) }
    catch (e) { setError(errText(e)) }
    finally { setBusy(false) }
  }

  useEffect(() => {
    api<Profile>("/search/profile").then((value) => {
      setProfile(value); onDefaultMode?.(value.default_mode)
      // при открытии — последняя сохранённая выдача, без запроса к бирже
      if (!settings) api<SearchResult | null>("/search/cache").then((cached) => {
        if (cached) { setProfile(cached.profile); show(cached) }
      }).catch((e) => setError(errText(e)))
    }).catch((e) => setError(errText(e)))
    // Фильтры загружаются при открытии раздела, ввод пользователя сохраняется до следующего поиска.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!profile) return <><div role="status" className={TOOLBAR}>{error || "Загружаем фильтры поиска…"}</div><div className={LIST_HEAD} /></>
  const set = (key: keyof Profile, value: string | number | boolean) => setProfile({ ...profile, [key]: value })
  const setRoute = (i: number, key: keyof Route, value: string | number) =>
    setProfile({ ...profile, routes: profile.routes.map((r, j) => j === i ? { ...r, [key]: value } : r) })
  const radius = (id: string, label: string, value: number, onChange: (v: number) => void) =>
    <div className="grid gap-1.5"><Label htmlFor={id}>{label}</Label><Input id={id} className="w-24" type="number" min={10} max={999} required value={value} onChange={(e) => onChange(Number(e.target.value))} /></div>
  const save = async () => {
    setBusy(true); setError("")
    try {
      await api("/search/profile", { method: "PUT", body: profile })
      toast.success("Фильтры общего поиска сохранены")
    } catch (e) { setError(errText(e)) }
    finally { setBusy(false) }
  }
  // На «Грузах» карточка в свёрнутом виде — одна строка кнопок той же высоты, что инструменты режима «Под мои машины»;
  // итог и листание — отдельной строкой над списком (TOOLBAR и LIST_HEAD — общие с Loads.tsx).
  return <><section className={TOOLBAR}>
    {settings && <h2 className="mb-3 font-semibold">Общий поиск грузов</h2>}
    <form onSubmit={(e) => { e.preventDefault(); void search(profile, 1, true) }} className="flex flex-col gap-4">
      {!settings && <div className="grid gap-2 sm:flex">
        <Button type="button" variant="outline" aria-expanded={expanded} aria-controls="advanced-search-filters" onClick={() => setExpanded(!expanded)}>Расширенный поиск</Button>
        <Button type="submit" disabled={busy}>{busy ? "Обновляем грузы…" : "Обновить грузы"}</Button>
      </div>}
      <div id="advanced-search-filters" hidden={!expanded} className={expanded ? "grid gap-3 sm:grid-cols-2 xl:grid-cols-3" : "hidden"}>
        <fieldset className="flex flex-col gap-2 sm:col-span-2 xl:col-span-3">
          <legend className="mb-1 text-sm font-medium">Направления</legend>
          {profile.routes.map((r, i) => <div key={i} className="flex flex-wrap items-end gap-2 rounded-lg bg-muted/40 p-2">
            <div className="grid min-w-40 flex-1 gap-1.5"><Label htmlFor={`from-${i}`}>Откуда</Label><CityInput id={`from-${i}`} required value={r.from_city} onChange={(v) => setRoute(i, "from_city", v)} /></div>
            {radius(`from-r-${i}`, "Радиус, км", r.from_radius, (v) => setRoute(i, "from_radius", v))}
            <span className="pb-2 text-muted-foreground" aria-hidden>→</span>
            <div className="grid min-w-40 flex-1 gap-1.5"><Label htmlFor={`to-${i}`}>Куда</Label><CityInput id={`to-${i}`} placeholder="Любое направление" value={r.to_city} onChange={(v) => setRoute(i, "to_city", v)} /></div>
            {radius(`to-r-${i}`, "Радиус, км", r.to_radius, (v) => setRoute(i, "to_radius", v))}
            {profile.routes.length > 1 && <Button type="button" variant="ghost" aria-label={`Убрать направление ${i + 1}`} onClick={() => setProfile({ ...profile, routes: profile.routes.filter((_, j) => j !== i) })}>Убрать</Button>}
          </div>)}
          {profile.routes.length < 10 && <Button type="button" variant="outline" className="self-start" onClick={() => setProfile({ ...profile, routes: [...profile.routes, { ...NEW_ROUTE }] })}>+ Направление</Button>}
        </fieldset>
        <div className="grid gap-1.5"><Label htmlFor="search-body">Кузов</Label><select id="search-body" className="h-9 rounded-md border bg-background px-3 text-sm" value={profile.body_mask} onChange={(e) => set("body_mask", Number(e.target.value))}>
          <option value={0}>Любой</option><option value={1}>Тент</option><option value={2}>Бортовой</option><option value={4}>Рефрижератор</option><option value={8}>Изотерм</option>
        </select></div>
        <div className="grid gap-1.5"><Label htmlFor="search-weight">Вес до, т</Label><Input id="search-weight" type="number" min={0.1} max={60} step={0.1} required value={profile.capacity} onChange={(e) => set("capacity", Number(e.target.value))} /></div>
        <div className="grid gap-1.5"><Label htmlFor="search-height">Высота до, м</Label><Input id="search-height" type="number" min={0.1} max={5} step={0.1} required value={profile.height} onChange={(e) => set("height", Number(e.target.value))} /></div>
        {settings && <div className="grid gap-1.5"><Label htmlFor="search-mode">При открытии «Грузов»</Label><select id="search-mode" className="h-9 rounded-md border bg-background px-3 text-sm" value={profile.default_mode} onChange={(e) => set("default_mode", e.target.value)}><option value="general">Общий поиск</option><option value="trucks">Под мои машины</option></select></div>}
        {settings && <div className="flex items-end gap-3 sm:col-span-2 xl:col-span-3">
          <Label className="flex h-9 items-center gap-2"><input type="checkbox" checked={!!profile.auto_refresh} onChange={(e) => set("auto_refresh", e.target.checked)} />Обновлять грузы по расписанию</Label>
          <div className="grid gap-1.5"><Label htmlFor="search-every">Раз в, часов</Label><Input id="search-every" className="w-24" type="number" min={1} max={24} required disabled={!profile.auto_refresh} value={profile.refresh_hours ?? 4} onChange={(e) => set("refresh_hours", Number(e.target.value))} /></div>
        </div>}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {settings && <Button type="button" className="self-start" onClick={save} disabled={busy}>Сохранить фильтры</Button>}
    </form>
  </section>
  {!settings && <div className={LIST_HEAD}>
    {total === null ? <span>Задайте фильтры и нажмите «Обновить грузы».</span> : <>
      {/* одна строка и на 375px — иначе при переключении режимов список уезжает */}
      <span className="whitespace-nowrap" title={updated && `Обновлено ${new Date(updated).toLocaleString("ru-RU")}`}>Найдено {total.toLocaleString("ru-RU")} · стр. {page}{updated && ` · ${new Date(updated).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`}</span>
      <span className="ml-auto flex gap-1"><Button type="button" size="icon" variant="outline" aria-label="Предыдущая страница" disabled={busy || page <= 1} onClick={() => search(profile, page - 1)}><ChevronLeft /></Button><Button type="button" size="icon" variant="outline" aria-label="Следующая страница" disabled={busy || !more} onClick={() => search(profile, page + 1)}><ChevronRight /></Button></span>
    </>}
  </div>}
  </>
}

export const TOOLBAR = "rounded-xl border bg-card p-4"
export const LIST_HEAD = "flex min-h-8 flex-wrap items-center gap-2 text-sm text-muted-foreground"
