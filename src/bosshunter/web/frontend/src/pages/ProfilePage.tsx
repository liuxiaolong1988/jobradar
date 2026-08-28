import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { TagsInput } from '@/components/ui/tags-input'
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Plus,
  Save,
  Sparkles,
  Target,
  Trash2,
  X,
} from 'lucide-react'

// ─── 类型（与后端 ai/profile.py profile_to_dict 对齐）──────────────

interface Anchor {
  name: string
  strength: string
  evidence: string
}

interface ProfileData {
  version: number
  positioning: string
  weights: Record<DimensionKey, number>
  dimension_notes: Record<DimensionKey, string>
  veto_titles: string[]
  veto_rules: string[]
  ai_signal_keywords: string[]
  ai_signal_max_bonus: number
  salary_range: string
  competence_anchors: Anchor[]
  search_keywords: string[]
  notes: string
  updated_at?: string
}

type DimensionKey = 'title_match' | 'reporting' | 'industry_scenario' | 'company_stage' | 'commute' | 'salary'

const DIMENSIONS: { key: DimensionKey; label: string; hint: string }[] = [
  { key: 'title_match', label: '职能匹配', hint: '岗位 title 与主 title 家族的匹配度' },
  { key: 'reporting', label: '汇报线', hint: '向谁汇报、职级定位' },
  { key: 'industry_scenario', label: '行业场景', hint: '行业、业务场景、转型阶段' },
  { key: 'company_stage', label: '企业阶段', hint: '企业规模、发展阶段、文化' },
  { key: 'commute', label: '通勤区位', hint: '办公地点与通勤圈' },
  { key: 'salary', label: '薪资', hint: '薪资区间匹配度' },
]

const DEFAULT_PROFILE: ProfileData = {
  version: 1,
  positioning: '',
  weights: {
    title_match: 30, reporting: 20, industry_scenario: 20,
    company_stage: 10, commute: 10, salary: 10,
  },
  dimension_notes: {
    title_match: '', reporting: '', industry_scenario: '',
    company_stage: '', commute: '', salary: '',
  },
  veto_titles: [],
  veto_rules: [],
  ai_signal_keywords: [],
  ai_signal_max_bonus: 10,
  salary_range: '',
  competence_anchors: [],
  search_keywords: [],
  notes: '',
}

const STRENGTH_OPTIONS = [
  { value: '强', label: '强' },
  { value: '中', label: '中' },
  { value: '弱', label: '弱' },
]

const cardClass = 'rounded-xl border border-card-border bg-white p-5 space-y-4'
const labelClass = 'block text-xs font-bold text-foreground mb-1.5'
const inputClass = 'w-full rounded-md border border-card-border bg-white px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary'
const textareaClass = 'w-full min-h-[72px] rounded-md border border-card-border bg-white px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary resize-y'

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/profile')
        const data = await res.json()
        if (data && typeof data === 'object') {
          setProfile({ ...DEFAULT_PROFILE, ...data })
        }
      } catch {
        setError('加载画像失败，请刷新重试')
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  const updateField = <K extends keyof ProfileData>(key: K, value: ProfileData[K]) => {
    setProfile(prev => (prev ? { ...prev, [key]: value } : prev))
  }

  const updateWeight = (key: DimensionKey, value: number) => {
    setProfile(prev => {
      if (!prev) return prev
      const clamped = Math.max(0, Math.min(100, Math.round(value) || 0))
      return { ...prev, weights: { ...prev.weights, [key]: clamped } }
    })
  }

  const updateNote = (key: DimensionKey, value: string) => {
    setProfile(prev => {
      if (!prev) return prev
      return { ...prev, dimension_notes: { ...prev.dimension_notes, [key]: value } }
    })
  }

  const updateAnchor = (index: number, patch: Partial<Anchor>) => {
    setProfile(prev => {
      if (!prev) return prev
      const anchors = prev.competence_anchors.map((a, i) => (i === index ? { ...a, ...patch } : a))
      return { ...prev, competence_anchors: anchors }
    })
  }

  const addAnchor = () => {
    setProfile(prev => {
      if (!prev) return prev
      return { ...prev, competence_anchors: [...prev.competence_anchors, { name: '', strength: '中', evidence: '' }] }
    })
  }

  const removeAnchor = (index: number) => {
    setProfile(prev => {
      if (!prev) return prev
      return { ...prev, competence_anchors: prev.competence_anchors.filter((_, i) => i !== index) }
    })
  }

  const handleGenerate = async () => {
    setGenerating(true)
    setError('')
    setNotice('')
    try {
      const res = await fetch('/api/profile/generate', { method: 'POST' })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error || '生成失败，请检查 AI 配置后重试')
        return
      }
      setProfile({ ...DEFAULT_PROFILE, ...data.profile })
      setNotice(data.message || '初版画像已生成，请检查并修订后保存')
    } catch {
      setError('生成请求失败，请重试')
    } finally {
      setGenerating(false)
    }
  }

  const handleSave = async () => {
    if (!profile) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const res = await fetch('/api/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error || '保存失败')
        return
      }
      setProfile({ ...DEFAULT_PROFILE, ...data.profile })
      setNotice('画像已保存，后续评分将按画像匹配')
    } catch {
      setError('保存请求失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm('删除画像后评分回退为「简历 vs JD」模式，确认删除？')) return
    setError('')
    setNotice('')
    try {
      const res = await fetch('/api/profile', { method: 'DELETE' })
      const data = await res.json()
      setProfile(null)
      setNotice(data.message || '画像已删除')
    } catch {
      setError('删除请求失败，请重试')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted">
        <Loader2 className="w-4 h-4 animate-spin" />
        加载目标画像…
      </div>
    )
  }

  // 未生成状态：引导生成
  if (!profile) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        {error && (
          <div className="flex items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            {error}
          </div>
        )}
        <div className={cardClass + ' text-center py-16'}>
          <div className="w-14 h-14 rounded-2xl bg-[#FFF0E5] text-primary flex items-center justify-center mx-auto">
            <Target className="w-7 h-7" />
          </div>
          <h2 className="mt-4 text-lg font-black text-foreground">尚未生成目标岗位画像</h2>
          <p className="mt-2 text-sm text-muted leading-6 max-w-md mx-auto">
            上传简历后，AI 会根据简历生成初版画像（六维权重、否决词、能力锚点），
            你可以逐项修订后保存。保存后，评分引擎将按「画像 vs JD」匹配，而非「简历 vs JD」。
          </p>
          <div className="mt-6 flex items-center justify-center gap-3">
            <Button onClick={() => void handleGenerate()} disabled={generating}>
              {generating
                ? <><Loader2 className="w-4 h-4 animate-spin" /> AI 生成中…</>
                : <><Sparkles className="w-4 h-4" /> 从简历生成初版画像</>}
            </Button>
          </div>
          <p className="mt-4 text-xs text-muted">
            需要先在「配置 → 简历设置」上传简历，并配置 AI API Key
          </p>
        </div>
      </div>
    )
  }

  const weightSum = DIMENSIONS.reduce((sum, d) => sum + (profile.weights[d.key] || 0), 0)

  return (
    <div className="max-w-4xl mx-auto space-y-4 pb-8">
      {notice && (
        <div className="flex items-start gap-2 rounded-xl bg-[#FFF0E5] px-4 py-3 text-sm text-primary">
          <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
          {notice}
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          {error}
        </div>
      )}

      {/* 定位与基础信息 */}
      <div className={cardClass}>
        <div className="flex items-center justify-between">
          <h2 className="text-base font-black text-foreground flex items-center gap-2">
            <Target className="w-4 h-4 text-primary" />
            目标岗位画像
            {profile.updated_at && (
              <span className="text-xs font-normal text-muted">（更新于 {profile.updated_at}）</span>
            )}
          </h2>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => void handleGenerate()} disabled={generating}>
              {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              重新生成
            </Button>
          </div>
        </div>
        <div>
          <label className={labelClass}>一句话定位（这个画像回答"我该投什么样的岗"）</label>
          <input
            className={inputClass}
            value={profile.positioning}
            onChange={e => updateField('positioning', e.target.value)}
            placeholder="例如：中型成长企业 IT 一号位，直接向老板汇报"
          />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelClass}>目标薪资区间</label>
            <input
              className={inputClass}
              value={profile.salary_range}
              onChange={e => updateField('salary_range', e.target.value)}
              placeholder="例如：3-5万/月 × 14薪"
            />
          </div>
          <div>
            <label className={labelClass}>AI 信号附加分上限（0-30）</label>
            <input
              type="number"
              min={0}
              max={30}
              className={inputClass}
              value={profile.ai_signal_max_bonus}
              onChange={e => updateField('ai_signal_max_bonus', Math.max(0, Math.min(30, Number(e.target.value) || 0)))}
            />
          </div>
        </div>
      </div>

      {/* 六维权重与评分说明 */}
      <div className={cardClass}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-black text-foreground">评分维度与权重</h3>
          <span className={`text-xs font-bold ${weightSum === 100 ? 'text-muted' : 'text-amber-600'}`}>
            当前合计 {weightSum}（不必恰好 100，按占比折算）
          </span>
        </div>
        {DIMENSIONS.map(dim => (
          <div key={dim.key} className="grid grid-cols-[120px_80px_1fr] gap-3 items-start">
            <div>
              <div className="text-sm font-bold text-foreground">{dim.label}</div>
              <div className="text-[11px] text-muted leading-4">{dim.hint}</div>
            </div>
            <input
              type="number"
              min={0}
              max={100}
              className="w-20 rounded-md border border-card-border bg-white px-2 py-1.5 text-sm text-center outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
              value={profile.weights[dim.key]}
              onChange={e => updateWeight(dim.key, Number(e.target.value))}
            />
            <textarea
              className={textareaClass}
              value={profile.dimension_notes[dim.key]}
              onChange={e => updateNote(dim.key, e.target.value)}
              placeholder="评分规则（会拼进 AI 评分标准），例如：命中 IT负责人/IT总监 满分；向总经理汇报 +…"
            />
          </div>
        ))}
      </div>

      {/* 否决规则 */}
      <div className={cardClass}>
        <h3 className="text-sm font-black text-foreground">一票否决</h3>
        <div>
          <label className={labelClass}>title 否决词（采集后粗筛直接过滤，省 AI 调用）</label>
          <TagsInput
            value={profile.veto_titles}
            onChange={tags => updateField('veto_titles', tags)}
            placeholder="如：运维工程师、网络管理员、后端、前端…"
          />
        </div>
        <div>
          <label className={labelClass}>JD 否决规则（AI 评分时判定，命中则 0 分）</label>
          <TagsInput
            value={profile.veto_rules}
            onChange={tags => updateField('veto_rules', tags)}
            placeholder="如：外包驻场、纯运维无管理、长期出差…"
          />
        </div>
      </div>

      {/* 能力锚点 */}
      <div className={cardClass}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-black text-foreground">能力锚点（人岗匹配辅助参考）</h3>
          <Button variant="secondary" size="sm" onClick={addAnchor}>
            <Plus className="w-4 h-4" /> 添加锚点
          </Button>
        </div>
        {profile.competence_anchors.length === 0 && (
          <p className="text-xs text-muted">暂无锚点，评分仅按画像维度判断</p>
        )}
        {profile.competence_anchors.map((anchor, i) => (
          <div key={i} className="flex items-start gap-2">
            <input
              className={inputClass + ' flex-1'}
              value={anchor.name}
              onChange={e => updateAnchor(i, { name: e.target.value })}
              placeholder="能力块名称，如：连锁零售 IT 治理"
            />
            <select
              className="rounded-md border border-card-border bg-white px-2 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/30"
              value={anchor.strength}
              onChange={e => updateAnchor(i, { strength: e.target.value })}
            >
              {STRENGTH_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <input
              className={inputClass + ' flex-[2]'}
              value={anchor.evidence}
              onChange={e => updateAnchor(i, { evidence: e.target.value })}
              placeholder="简历证据，如：6000 店连锁治理、ERP 选型落地"
            />
            <button
              type="button"
              onClick={() => removeAnchor(i)}
              className="mt-1.5 text-muted hover:text-danger"
              aria-label="删除锚点"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        ))}
      </div>

      {/* 采集关键词与备注 */}
      <div className={cardClass}>
        <h3 className="text-sm font-black text-foreground">采集搜索关键词（建议）</h3>
        <TagsInput
          value={profile.search_keywords}
          onChange={tags => updateField('search_keywords', tags)}
          placeholder="如：IT负责人、IT总监、信息化总监…"
        />
        <div>
          <label className={labelClass}>备注（仅自己看，不进评分）</label>
          <textarea
            className={textareaClass}
            value={profile.notes}
            onChange={e => updateField('notes', e.target.value)}
          />
        </div>
      </div>

      {/* 操作栏 */}
      <div className="flex items-center justify-between sticky bottom-0 rounded-xl border border-card-border bg-white/95 backdrop-blur px-5 py-4 shadow-lg">
        <Button variant="destructive" size="sm" onClick={() => void handleDelete()}>
          <Trash2 className="w-4 h-4" /> 删除画像（回退简历匹配）
        </Button>
        <Button onClick={() => void handleSave()} disabled={saving}>
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          保存画像
        </Button>
      </div>
    </div>
  )
}
