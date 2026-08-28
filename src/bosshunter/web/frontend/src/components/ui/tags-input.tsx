import { useState, KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TagsInputProps {
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  className?: string
  onAdd?: (tag: string) => void
}

export function TagsInput({ value, onChange, placeholder = '输入后按回车添加', className, onAdd }: TagsInputProps) {
  const [input, setInput] = useState('')

  // 把输入框里的残留文本拆成标签并提交：
  // 按逗号（英文, 中文，）、分号、换行切分，每段 trim 后逐个入列，
  // 支持"数字化负责人,CIO,技术负责人"这种手填习惯。
  const commitBulk = (raw: string) => {
    const clean = raw.trim()
    if (!clean) return ''
    const parts = clean.split(/[,，;；\n]+/).map(s => s.trim()).filter(Boolean)
    if (!parts.length) return ''
    if (onAdd) {
      parts.forEach(p => onAdd(p))
    } else {
      const next = [...value]
      parts.forEach(p => { if (!next.includes(p)) next.push(p) })
      onChange(next)
    }
    return ''
  }

  // 兜底提交：输入未按回车、直接失焦（点保存/点别处）时，残留文本也转为标签，
  // 避免"填了词但保存时丢失"的坑（魔王大人 2026-08-28 实测踩到）。
  const commitInput = () => {
    setInput(commitBulk(input))
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      commitInput()
    } else if (e.key === 'Backspace' && !input && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  // 输入过程中检测到分隔符（逗号/分号），当场拆分并提交，
  // 让"打了个逗号还在继续敲下一个"的手感也能对上。
  const handleChange = (raw: string) => {
    if (/[,，;；]/.test(raw)) {
      const leftover = commitBulk(raw)
      setInput(leftover)
    } else {
      setInput(raw)
    }
  }

  const removeTag = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
  }

  return (
    <div className={cn(
      'flex flex-wrap gap-1.5 min-h-[36px] p-2 rounded-md border border-card-border bg-white focus-within:ring-2 focus-within:ring-primary/30 focus-within:border-primary',
      className
    )}>
      {value.map((tag, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-md bg-[#FFF0E5] px-2 py-0.5 text-xs font-bold text-primary"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(i)}
            className="text-primary/70 hover:text-primary"
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={e => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commitInput}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[80px] bg-transparent text-sm text-foreground placeholder:text-muted/60 outline-none"
      />
    </div>
  )
}
