import { useEffect, useState } from "react"
import { toast } from "sonner"
import { CheckCircle2, Copy, Loader2, Phone, Plus, Save, Trash2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api, ago, errText, num, usePoll, type Settings } from "@/lib/api"

type Field = { k: string; label: string; hint?: string; type: "number" | "bool" | "text" | "long" | "list" }
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
    { k: "default_goal", label: "Сценарий по умолчанию", hint: "подставляется в окне «Поручить агенту»; {min_rate_km} заменится на «Минимум ₽/км чистыми»", type: "long" },
    { k: "goal_presets", label: "Готовые сценарии", hint: "кнопки в окне «Поручить агенту»: логист выбирает, что узнать", type: "list" },
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
  const empty = s ? GROUPS.flatMap((g) => g.fields).filter((f) => f.type === "number" && f.k in s && s[f.k] === null) : []
  const save = async () => {
    if (!s) return
    if (empty.length) { toast.error(`Заполните: ${empty.map((f) => f.label).join(", ")}`); return }
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
          <Costs />
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
              if (f.type === "list") return <ListField key={f.k} f={f} value={(s[f.k] as string[]) ?? []} onChange={(v) => set(f.k, v)} />
              if (f.type === "long") return (
                <div key={f.k} className="flex flex-col gap-1.5 sm:col-span-2">
                  <Label htmlFor={id}>{f.label}</Label>
                  {f.hint && <span className="text-xs text-muted-foreground">{f.hint}</span>}
                  <Textarea id={id} rows={4} value={String(s[f.k] ?? "")} onChange={(e) => set(f.k, e.target.value)} />
                </div>
              )
              return (
                <div key={f.k} className="flex flex-col gap-1.5">
                  <Label htmlFor={id}>{f.label}</Label>
                  <Input id={id} type={f.type} value={String(s[f.k] ?? "")} placeholder={f.hint}
                    aria-invalid={f.type === "number" && s[f.k] === null}
                    onChange={(e) => set(f.k, f.type === "number" ? num(e.target.value) : e.target.value)} />
                </div>
              )
            })}
          </div>
        </section>
      ))}
      <PhoneSettings />
      <DncList />
    </div>
  )
}

function ListField({ f, value, onChange }: { f: Field; value: string[]; onChange: (v: string[]) => void }) {
  const put = (i: number, v: string) => onChange(value.map((x, j) => (j === i ? v : x)))
  return (
    <div className="flex flex-col gap-1.5 sm:col-span-2">
      <Label>{f.label}</Label>
      {f.hint && <span className="text-xs text-muted-foreground">{f.hint}</span>}
      {value.map((x, i) => (
        <div key={i} className="flex items-start gap-2">
          <Textarea rows={2} aria-label={`${f.label} ${i + 1}`} value={x} onChange={(e) => put(i, e.target.value)} />
          <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label={`Удалить сценарий ${i + 1}`}
            onClick={() => onChange(value.filter((_, j) => j !== i))}><X /></Button>
        </div>
      ))}
      <Button variant="outline" size="sm" className="self-start" disabled={value.length >= 20} onClick={() => onChange([...value, ""])}>
        <Plus />Добавить сценарий
      </Button>
    </div>
  )
}

type Dnc = { phone: string; reason: string; created: number; by_login: string }

type PhoneT = { mode: "off" | "megafon" | "mock"; url: string; user: string; key_set: boolean; key_hint: string; crm_token: string
  api: "legacy" | "v1" | "cp" | null; ready: boolean; why: string; webhook_url: string; webhook_seen: number | null
  number: string; cp_template: string }
const MODES: { id: PhoneT["mode"]; label: string; hint: string }[] = [
  { id: "off", label: "Выключена", hint: "агент не звонит" },
  { id: "megafon", label: "МегаФон", hint: "звонки через вашу виртуальную АТС" },
  { id: "mock", label: "Имитация", hint: "для проверки: звонки и расшифровки не настоящие" },
]

function CopyRow({ id, label, value, multiline }: { id: string; label: string; value: string; multiline?: boolean }) {
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); toast.success("Скопировано") }
    catch { toast.info("Выделите и скопируйте вручную") }
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        {multiline
          ? <textarea id={id} readOnly value={value} rows={4} onFocus={(e) => e.currentTarget.select()}
              className="w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs" />
          : <Input id={id} readOnly value={value} onFocus={(e) => e.currentTarget.select()} className="font-mono text-xs" />}
        <Button type="button" variant="outline" onClick={copy} aria-label={`Скопировать: ${label}`}><Copy />Скопировать</Button>
      </div>
    </div>
  )
}

/** Подключение ВАТС МегаФон прямо из программы: ключи вставляет владелец, по переписке они не ходят.
 *  Названия полей — как в кабинете ВАТС: «Настройки» → «Интеграция с CRM» → «Ваша CRM / REST API». */
function PhoneSettings() {
  const [p, setP] = useState<PhoneT | null>(null)
  const [f, setF] = useState({ mode: "off" as PhoneT["mode"], url: "", user: "", key: "", number: "" })
  const [busy, setBusy] = useState<"" | "save" | "check">("")
  const [check, setCheck] = useState<{ ok: boolean; text: string } | null>(null)
  const apply = (v: PhoneT) => { setP(v); setF({ mode: v.mode, url: v.url, user: v.user, key: "", number: v.number }) }
  useEffect(() => { api<PhoneT>("/phone").then(apply).catch((e) => toast.error(errText(e))) }, [])
  const save = async () => {
    setBusy("save"); setCheck(null)
    try {
      const body: Record<string, string> = { mode: f.mode, url: f.url, user: f.user, number: f.number }
      if (f.key.trim()) body.key = f.key
      apply(await api<PhoneT>("/phone", { method: "PUT", body })); toast.success("Телефония сохранена")
    } catch (e) { toast.error(errText(e)) } finally { setBusy("") }
  }
  const test = async () => {
    setBusy("check"); setCheck(null)
    try {
      const r = await api<{ employees: number; bot_found: boolean; api: string; number?: string }>("/phone/check", { method: "POST" })
      setCheck({ ok: r.bot_found, text: r.bot_found ? `Подключено: ВАТС на связи, сотрудников ${r.employees}, бот найден${r.number ? `, звоним с номера +${r.number}` : ""}.`
        : `ВАТС на связи (сотрудников ${r.employees}), но сотрудника «${f.user}» среди них нет — проверьте логин бота.` })
      setP(await api<PhoneT>("/phone"))
    } catch (e) { setCheck({ ok: false, text: errText(e) }) } finally { setBusy("") }
  }
  if (!p) return null
  const dirty = f.mode !== p.mode || f.url !== p.url || f.user !== p.user || f.number !== p.number || !!f.key
  const old = p.api === "legacy" || p.api === "v1"  // старый кабинет МегаФона (megapbx); новый — vatsNNN.cp.megafon.ru
  return (
    <section className="rounded-xl border bg-card p-5" id="phone">
      <h2 className="mb-1 flex items-center gap-2 font-medium"><Phone className="size-4" />Телефония</h2>
      <p className="mb-4 text-sm text-muted-foreground">Звонки агента через виртуальную АТС МегаФон (кабинет vats…cp.megafon.ru). Нужен администратор ВАТС.</p>
      <div className="mb-4 flex flex-wrap gap-2" role="radiogroup" aria-label="Режим телефонии">
        {MODES.map((m) => (
          <button key={m.id} type="button" role="radio" aria-checked={f.mode === m.id} onClick={() => setF({ ...f, mode: m.id })}
            className={`rounded-lg border px-3 py-2 text-left text-sm ${f.mode === m.id ? "border-primary bg-accent text-primary" : "hover:bg-muted"}`}>
            <div className="font-medium">{m.label}</div><div className="text-xs text-muted-foreground">{m.hint}</div>
          </button>
        ))}
      </div>
      {f.mode === "megafon" && (
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-3">
            <div className="text-sm font-medium">1. Скопируйте сюда из кабинета ВАТС («Настройки» → «Настройки API»)</div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5 sm:col-span-2">
                <Label htmlFor="ph-key">Ключ API</Label>
                <Input id="ph-key" type="password" autoComplete="off" value={f.key} placeholder={p.key_set ? `сохранён ${p.key_hint} — оставьте пустым` : "длинный ключ из «Настройки API» (начинается с eyJ…)"}
                  onChange={(e) => setF({ ...f, key: e.target.value })} />
                <span className="text-xs text-muted-foreground">В «Настройки API» должен быть добавлен IP сервера программы.</span>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ph-user">Сотрудник-бот</Label>
                <Input id="ph-user" value={f.user} placeholder="логин, ФИО или внутренний номер" onChange={(e) => setF({ ...f, user: e.target.value })} />
                <span className="text-xs text-muted-foreground">Сотрудник ВАТС, с которого агент звонит.</span>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ph-num">Номер для исходящих</Label>
                <Input id="ph-num" value={f.number} inputMode="tel" placeholder="необязательно — первый номер ВАТС" onChange={(e) => setF({ ...f, number: e.target.value })} />
                <span className="text-xs text-muted-foreground">Этот номер увидит заказчик.</span>
              </div>
              {old && <div className="flex flex-col gap-1.5 sm:col-span-2">
                <Label htmlFor="ph-url">Адрес кабинета ВАТС (только старый кабинет)</Label>
                <Input id="ph-url" value={f.url} placeholder="vats000000.megapbx.ru" onChange={(e) => setF({ ...f, url: e.target.value })} />
              </div>}
            </div>
          </div>
          <div className="flex flex-col gap-3">
            {old ? <>
              <div className="text-sm font-medium">2. Впишите в кабинет ВАТС, в настройки «Интеграция с CRM»</div>
              <CopyRow id="ph-hook" label="Адрес вашей CRM" value={p.webhook_url} />
              <CopyRow id="ph-crm" label="Ключ для авторизации в вашей CRM" value={p.crm_token} />
            </> : <>
              <div className="text-sm font-medium">2. Чтобы программа узнавала, чем закончился звонок</div>
              <p className="text-sm text-muted-foreground">В кабинете ВАТС: «Уведомления» → добавить уведомление → событие «Завершение звонка», способ — HTTP, метод POST, формат JSON. Вставьте адрес и текст ниже.</p>
              <CopyRow id="ph-hook" label="Адрес (URL)" value={p.webhook_url} />
              <CopyRow id="ph-tpl" label="Текст уведомления (тело запроса)" value={p.cp_template} multiline />
            </>}
            <span className="text-xs text-muted-foreground">{p.webhook_seen ? `ВАТС уже присылала уведомления о звонках — ${ago(p.webhook_seen)}.` : "ВАТС ещё не присылала уведомлений о звонках."}</span>
          </div>
        </div>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button type="button" onClick={save} disabled={!!busy || !dirty}>{busy === "save" ? <Loader2 className="animate-spin" /> : <Save />}Сохранить</Button>
        {p.mode === "megafon" && <Button type="button" variant="outline" onClick={test} disabled={!!busy || dirty}>
          {busy === "check" ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}3. Проверить подключение</Button>}
        {dirty && f.mode === "megafon" && <span className="text-xs text-muted-foreground">сначала сохраните</span>}
      </div>
      {p.mode === "megafon" && !p.ready && <p className="mt-3 text-sm text-destructive">{p.why}</p>}
      {check && <p role="status" className={`mt-3 text-sm ${check.ok ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}>{check.text}</p>}
    </section>
  )
}

function DncList() {
  const { data, reload } = usePoll<Dnc[]>("/dnc", 30000)
  const [phone, setPhone] = useState("")
  const add = async () => {
    try { await api("/dnc", { body: { phone, reason: "вручную" } }); setPhone(""); await reload() } catch (e) { toast.error(errText(e)) }
  }
  const del = async (p: string) => {
    try { await api(`/dnc/${encodeURIComponent(p)}`, { method: "DELETE" }); await reload() } catch (e) { toast.error(errText(e)) }
  }
  return (
    <section className="rounded-xl border bg-card p-5">
      <h2 className="mb-1 font-medium">Стоп-лист звонков</h2>
      <p className="mb-4 text-sm text-muted-foreground">Сюда номер попадает сам, если в разговоре попросили не звонить.</p>
      <div className="mb-3 flex gap-2">
        <Input aria-label="Телефон в стоп-лист" placeholder="+7 921 000-00-00" value={phone} onChange={(e) => setPhone(e.target.value)} />
        <Button variant="outline" onClick={add} disabled={!phone.trim()}>Добавить</Button>
      </div>
      <div className="flex flex-col gap-1 text-sm">
        {!data?.length && <div className="text-muted-foreground">Пусто</div>}
        {data?.map((x) => (
          <div key={x.phone} className="flex items-center gap-2">
            <span className="num font-medium">{x.phone}</span>
            <span className="truncate text-muted-foreground">{x.reason} · {x.by_login} · {ago(x.created)}</span>
            <Button size="icon" variant="ghost" className="ml-auto size-7" aria-label={`Убрать ${x.phone}`} onClick={() => del(x.phone)}><Trash2 /></Button>
          </div>
        ))}
      </div>
    </section>
  )
}

type CostsT = { llm_requests: number; llm_tokens: number; llm_rub: number; calls: number; call_minutes: number; calls_rub: number; voice_tests?: number; voice_rub?: number }

function Costs() {
  const { data: c } = usePoll<CostsT>("/costs", 60000)
  if (!c) return null
  return (
    <p className="mt-1 text-sm">
      Сегодня: модель — <span className="num font-medium">{c.llm_rub} ₽</span> ({c.llm_requests} запросов, {c.llm_tokens.toLocaleString("ru-RU")} токенов),
      звонки — <span className="num font-medium">{c.calls_rub} ₽</span> ({c.calls} шт., {c.call_minutes} мин)
      {!!c.voice_tests && <>, тестовые голосовые — <span className="num font-medium">≈{c.voice_rub} ₽</span> ({c.voice_tests} шт.)</>}
    </p>
  )
}
