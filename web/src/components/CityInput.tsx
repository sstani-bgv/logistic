import { useEffect, useId, useState } from "react"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

/** Поле города с подсказками из справочника АТИ: «Краснодар, Краснодарский край» — без тёзок из соседних районов. */
export function CityInput({ value, onChange, ...props }: Omit<React.ComponentProps<"input">, "value" | "onChange" | "list"> & { value: string; onChange: (v: string) => void }) {
  const list = useId()
  const [hints, setHints] = useState<string[]>([])
  useEffect(() => {
    const q = value.trim()
    if (q.length < 2 || hints.includes(value)) return
    const t = setTimeout(() => api<string[]>(`/cities?q=${encodeURIComponent(q)}`).then(setHints).catch(() => {}), 300)
    return () => clearTimeout(t)
  }, [value]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <Input {...props} autoComplete="off" list={list} value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id={list}>{hints.map((h) => <option key={h} value={h} />)}</datalist>
    </>
  )
}
