import { useCallback, useEffect, useState } from "react"

export type Reason = { t: string; d: number; kind: "plus" | "minus" | "block" | "info" }
export type Metrics = { dist: number; dead: number; net: number | null; gross: number | null; per_km: number | null; money: string }
export type Firm = {
  id: string; name: string; profile?: string; claims?: number; isConfirmedShipper?: boolean
  rating?: { score: number }; contacts?: { id: number; name: string; phones?: { number: string }[] }[]
}
export type Raw = {
  id: string; loadNumber?: string; note?: string; addDate: string; changeDate?: string; similarCount?: number
  route: { distance: number }
  load: { weight: number; volume: number; height: number; cargoType: string }
  loading: { firstDate: string; lastDate: string; location: { city: string; region: string; street: string } }
  unloading: { location: { city: string; region: string; street: string } }
  rate: { price?: number; moneyType: number }
  firm?: Firm
}
export type Load = {
  id: string; truck_id: number; score: number; verdict: string; status: string; reject_reason?: string
  first_seen: number; reasons: Reason[]; metrics: Metrics; raw: Raw
}
export type Truck = {
  id: number; name: string; driver: string; city_name: string; radius: number; body_mask: number; body_name: string
  height: number; capacity: number; free_from: string; active: number; relevant: number
}
export type Msg = { id: string; mine: number; text: string; ts: number }
export type Dialog = {
  id: number; load_id: string; goal: string; status: string; summary: string; auto: number; updated: number
  facts: Record<string, string | null>; load: Load | null; messages: Msg[]
}
export type Rule = { id: number; match: string; value: string; block: number; delta: number; text: string; source: string; active: number; created: number }
export type LogItem = { ts: number; text: string; kind: string }
export type Stats = {
  seen: number; relevant: number; filtered: number; views: number; scans: number; dialogs: number; ready: number
  rules: number; hours_saved: number; me: { name?: string; ati_id?: string }; log: LogItem[]
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (!r.ok) {
    const e = await r.json().catch(() => ({}))
    throw new Error(e.detail ?? `Ошибка ${r.status}`)
  }
  return r.json()
}

/** Данные с автообновлением: биржа и переписка живут своей жизнью. */
export function usePoll<T>(path: string, ms = 5000) {
  const [data, setData] = useState<T | null>(null)
  const reload = useCallback(() => api<T>(path).then(setData).catch(() => {}), [path])
  useEffect(() => {
    reload()
    const t = setInterval(reload, ms)
    return () => clearInterval(t)
  }, [reload, ms])
  return { data, reload }
}

export const rub = (n?: number | null) => (n ? `${Math.round(n).toLocaleString("ru-RU")} ₽` : "—")
export const ddmm = (s?: string) => (s ? new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "")
export const ago = (ts: number) => {
  if (ts > 1e12) ts /= 1e6 // мессенджер АТИ отдаёт микросекунды
  const m = Math.round((Date.now() / 1000 - ts) / 60)
  return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : `${Math.round(m / 60)} ч назад`
}
export const atiLink = (id: string) => `https://loads.ati.su/loadinfo/${id}`
