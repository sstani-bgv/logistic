import { useEffect, useState } from "react"
import { toast } from "sonner"
import { api, errText, type Load } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

type Profile = {
  city_name: string; to_city_name: string; radius: number; body_mask: number
  capacity: number; height: number; default_mode: "general" | "trucks"
}
export type SearchResult = { loads: Load[]; total: number; page: number; public_only: boolean }

export default function SearchPanel({ settings = false, onResults, onDefaultMode }: {
  settings?: boolean
  onResults?: (result: SearchResult) => void
  onDefaultMode?: (mode: "general" | "trucks") => void
}) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState<number | null>(null)
  const [error, setError] = useState("")
  const [expanded, setExpanded] = useState(settings)

  async function search(value: Profile, nextPage = 1) {
    setBusy(true); setError("")
    try {
      const result = await api<SearchResult>("/search", { method: "POST", body: { ...value, page: nextPage } })
      setPage(result.page); setTotal(result.total); onResults?.(result)
    } catch (e) { setError(errText(e)) }
    finally { setBusy(false) }
  }

  useEffect(() => {
    api<Profile>("/search/profile").then((value) => {
      setProfile(value); onDefaultMode?.(value.default_mode)
      if (!settings) void search(value)
    }).catch((e) => setError(errText(e)))
    // Фильтры загружаются при открытии раздела, ввод пользователя сохраняется до следующего поиска.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!profile) return <div role="status">{error || "Загружаем фильтры поиска…"}</div>
  const set = (key: keyof Profile, value: string | number) => setProfile({ ...profile, [key]: value })
  const save = async () => {
    setBusy(true); setError("")
    try {
      await api("/search/profile", { method: "PUT", body: profile })
      toast.success("Фильтры общего поиска сохранены")
    } catch (e) { setError(errText(e)) }
    finally { setBusy(false) }
  }
  return <section className="rounded-xl border bg-card p-4">
    {settings && <h2 className="mb-3 font-semibold">Общий поиск грузов</h2>}
    {!settings && <Button type="button" variant="outline" className="mb-3" aria-expanded={expanded} aria-controls="advanced-search-filters" onClick={() => setExpanded(!expanded)}>Расширенный поиск</Button>}
    <form onSubmit={(e) => { e.preventDefault(); void search(profile) }} className="flex flex-col gap-4">
      <div id="advanced-search-filters" hidden={!expanded} className={expanded ? "grid gap-3 sm:grid-cols-2 xl:grid-cols-3" : "hidden"}>
        <div className="grid gap-1.5"><Label htmlFor="search-from">Откуда</Label><Input id="search-from" required value={profile.city_name} onChange={(e) => set("city_name", e.target.value)} /></div>
        <div className="grid gap-1.5"><Label htmlFor="search-to">Куда</Label><Input id="search-to" placeholder="Любое направление" value={profile.to_city_name} onChange={(e) => set("to_city_name", e.target.value)} /></div>
        <div className="grid gap-1.5"><Label htmlFor="search-radius">Радиус, км</Label><Input id="search-radius" type="number" min={10} max={2000} required value={profile.radius} onChange={(e) => set("radius", Number(e.target.value))} /></div>
        <div className="grid gap-1.5"><Label htmlFor="search-body">Кузов</Label><select id="search-body" className="h-9 rounded-md border bg-background px-3 text-sm" value={profile.body_mask} onChange={(e) => set("body_mask", Number(e.target.value))}>
          <option value={0}>Любой</option><option value={1}>Тент</option><option value={2}>Бортовой</option><option value={4}>Рефрижератор</option><option value={8}>Изотерм</option>
        </select></div>
        <div className="grid gap-1.5"><Label htmlFor="search-weight">Вес до, т</Label><Input id="search-weight" type="number" min={0.1} max={60} step={0.1} required value={profile.capacity} onChange={(e) => set("capacity", Number(e.target.value))} /></div>
        <div className="grid gap-1.5"><Label htmlFor="search-height">Высота до, м</Label><Input id="search-height" type="number" min={0.1} max={5} step={0.1} required value={profile.height} onChange={(e) => set("height", Number(e.target.value))} /></div>
        {settings && <div className="grid gap-1.5"><Label htmlFor="search-mode">При открытии «Грузов»</Label><select id="search-mode" className="h-9 rounded-md border bg-background px-3 text-sm" value={profile.default_mode} onChange={(e) => set("default_mode", e.target.value)}><option value="general">Общий поиск</option><option value="trucks">Под мои машины</option></select></div>}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap items-center gap-3">
        {settings ? <Button type="button" onClick={save} disabled={busy}>Сохранить фильтры</Button> : <>
          <Button type="submit" disabled={busy}>{busy ? "Ищем грузы…" : "Найти грузы"}</Button>
          {total !== null && <span className="text-sm text-muted-foreground">Найдено {total.toLocaleString("ru-RU")} · страница {page}</span>}
          {total !== null && <><Button type="button" variant="outline" disabled={busy || page <= 1} onClick={() => search(profile, page - 1)}>Назад</Button><Button type="button" variant="outline" disabled={busy || page * 10 >= total || page >= 100} onClick={() => search(profile, page + 1)}>Далее</Button></>}
        </>}
      </div>
    </form>
  </section>
}
