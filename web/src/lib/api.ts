import { useCallback, useEffect, useRef, useState } from "react"

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
  height: number; capacity: number; free_from: string; active: number; relevant: number; status: "free" | "busy"
}
export type Msg = { id: string; mine: number; text: string; ts: number; kind?: "text" | "call" }
export type Call = {
  id: number; dialog_id: number; phone: string; started: number | null; created: number; duration: number
  status: "queued" | "ringing" | "talking" | "done" | "no_answer" | "busy" | "failed"
  recording_url: string | null; transcript: string | null; cost: number; error: string | null; by_login: string | null
}
export type Dialog = {
  id: number; load_id: string; chat_id: string | null; goal: string; status: string; summary: string; auto: number; updated: number
  facts: Record<string, string | null>; load: Load | null; messages: Msg[]; calls?: Call[]; phone?: string | null; draft?: string | null
}
export type Rule = { id: number; match: string; value: string; block: number; delta: number; text: string; source: string; active: number; created: number }
export type LogItem = { ts: number; text: string; kind: string }
export type Stats = {
  seen: number; relevant: number; filtered: number; views: number; scans: number; dialogs: number; ready: number
  rules: number; hours_saved: number; me: { name?: string; ati_id?: string }; log: LogItem[]
  health?: Health
}
export type Problem = { part: "ati" | "llm" | "phone" | "backup" | "agent"; text: string; level: "error" | "warn" | "info" }
export type Health = { problems: Problem[]; ati_ok: boolean | null; ati_last_scan: number; telephony: "off" | "mock" | "megafon" }

export type DialogPatchResult = { ok: boolean; truck?: Truck | null }
export type Role = "owner" | "logist"
export type User = { id: number; login: string; role: Role; active: number }
export type Action = { id: number; ts: number; user_id: number | null; login: string; action: string; ref: string | null; detail: string | null }
export type Settings = {
  vat: number; min_rate_km: number; threshold: number; scan_minutes: number; pages: number; send_enabled: boolean
  auto_start: boolean; auto_score: number; auto_per_cycle: number; auto_daily: number; default_goal: string; goal_presets: string[]
  [k: string]: unknown
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

/** Сессия истекла или вход не выполнен — App показывает страницу входа. */
export const AUTH_EVENT = "auth:required"

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  })
  if (!r.ok) {
    const e = await r.json().catch(() => ({}))
    if (r.status === 401 && path !== "/login") window.dispatchEvent(new Event(AUTH_EVENT))
    const detail = Array.isArray(e.detail) ? e.detail.map((x: { msg?: string }) => x.msg).join("; ") : e.detail
    throw new ApiError(detail ?? `Ошибка ${r.status}`, r.status)
  }
  return r.json()
}

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Данные с автообновлением: биржа и переписка живут своей жизнью. Устаревшие ответы отбрасываются. */
export function usePoll<T>(path: string, ms = 5000) {
  const [data, setData] = useState<T | null>(null)
  const [offline, setOffline] = useState(false)
  const seq = useRef(0)
  const reload = useCallback(async () => {
    const n = ++seq.current
    try {
      const d = await api<T>(path)
      if (n === seq.current) { setData(d); setOffline(false) }
    } catch (e) {  // 401 обработает App; остальное — показать «нет связи», данные не выдавать за свежие
      if (n === seq.current && !(e instanceof ApiError && e.status === 401)) setOffline(true)
    }
  }, [path])
  useEffect(() => {
    setData(null)  // другой список/фильтр — старые карточки не показываем под новым заголовком
    reload()
    const t = setInterval(reload, ms)
    return () => clearInterval(t)
  }, [reload, ms])
  return { data, reload, offline }
}

/** Подтверждение необратимого действия (звонок, «Берём», отправка заказчику, удаление). */
export const ask = (text: string) => window.confirm(text)

/** Число из поля ввода: пусто/мусор → null (не 0), чтобы не уехало «радиус 0» или «звонить через 0 минут». */
export const num = (s: string): number | null => (s.trim() === "" || !isFinite(Number(s)) ? null : Number(s))

export const rub = (n?: number | null) => (n ? `${Math.round(n).toLocaleString("ru-RU")} ₽` : "—")
export const ddmm = (s?: string | null) => {
  const d = s ? new Date(`${s.slice(0, 10)}T00:00:00`) : null // локальная полночь, без сдвига UTC
  return d && !isNaN(d.getTime()) ? d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : ""
}
export const ago = (ts: number) => {
  if (ts > 1e12) ts /= 1e6 // мессенджер АТИ отдаёт микросекунды
  const m = Math.round((Date.now() / 1000 - ts) / 60)
  return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 48 * 60 ? `${Math.round(m / 60)} ч назад` : `${Math.round(m / 1440)} дн назад`
}
export const atiLink = (id: string) => `https://loads.ati.su/loadinfo/${id}`
