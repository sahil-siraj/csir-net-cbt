import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  Award,
  BarChart3,
  Bookmark,
  BookOpen,
  Calculator,
  CheckCircle2,
  Clock,
  Database,
  Flame,
  FlaskConical,
  LogOut,
  Play,
  SlidersHorizontal,
  Target,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import ThemeToggle from '../components/ThemeToggle'

function shortenUnitLabel(unit) {
  if (!unit) return ''
  if (unit === 'General Aptitude') return 'Aptitude'
  const m = unit.match(/^(Unit\s+\d+):\s*(.+)$/i)
  if (!m) return unit.slice(0, 18)
  const shortTitle = m[2].length > 18 ? `${m[2].slice(0, 16)}…` : m[2]
  return `${m[1]} (${shortTitle})`
}

function formatDate(iso) {
  if (!iso) return '—'
  const normalized = iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z'
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const SOURCE_OPTIONS = [
  { value: 'mixed', label: 'Mixed (PYQ + AI)' },
  { value: 'pyq_only', label: 'PYQ Only' },
  { value: 'ai_only', label: 'AI Verified Only' },
]

export default function DashboardPage() {
  const { user, logout } = useAuth()
  const { theme } = useTheme()
  const navigate = useNavigate()

  const [meta, setMeta] = useState(null)
  const [analytics, setAnalytics] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [launching, setLaunching] = useState('')

  // Full Mock builder state
  const [fullMockSource, setFullMockSource] = useState('mixed')

  // Custom Unit Practice builder state
  const [selectedUnits, setSelectedUnits] = useState([])
  const [selectedParts, setSelectedParts] = useState(['A', 'B', 'C'])
  const [questionCount, setQuestionCount] = useState(25)
  const [practiceSource, setPracticeSource] = useState('mixed')
  const [useTimer, setUseTimer] = useState(true)
  const [durationMinutes, setDurationMinutes] = useState(30)
  const [instantFeedback, setInstantFeedback] = useState(true)
  const [enableCalculator, setEnableCalculator] = useState(() => {
    try {
      return localStorage.getItem('csir_enable_calculator') !== 'false'
    } catch {
      return true
    }
  })

  const toggleCalculatorPref = () => {
    setEnableCalculator((prev) => {
      const next = !prev
      try {
        localStorage.setItem('csir_enable_calculator', String(next))
      } catch {
        // ignore storage errors
      }
      return next
    })
  }

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const [metaRes, analyticsRes] = await Promise.all([
          api.getTestsMeta(),
          api.getAnalytics(),
        ])
        if (!active) return
        setMeta(metaRes)
        setAnalytics(analyticsRes)
      } catch (err) {
        if (active) setError(err.message || 'Failed to load dashboard data')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => {
      active = false
    }
  }, [])

  const unitMasteryData = useMemo(() => {
    const units = meta?.syllabus_units || []
    const weakMap = new Map(
      (analytics?.weak_units || []).map((u) => [u.syllabus_unit, u])
    )
    const rows = units.map((unit) => {
      const stat = weakMap.get(unit)
      return {
        syllabus_unit: unit,
        shortLabel: shortenUnitLabel(unit),
        accuracy: stat ? stat.accuracy : 0,
        evaluated: stat ? stat.evaluated : 0,
        correct: stat ? stat.correct : 0,
        incorrect: stat ? stat.incorrect : 0,
        attempted: Boolean(stat && stat.evaluated > 0),
      }
    })
    // Highlight weakest attempted units first, then unattempted
    return rows.sort((a, b) => {
      if (a.attempted && !b.attempted) return -1
      if (!a.attempted && b.attempted) return 1
      if (a.attempted && b.attempted) return a.accuracy - b.accuracy
      return 0
    })
  }, [meta, analytics])

  const toggleUnit = (unit) => {
    setSelectedUnits((prev) =>
      prev.includes(unit) ? prev.filter((u) => u !== unit) : [...prev, unit]
    )
  }

  const togglePart = (part) => {
    setSelectedParts((prev) => {
      if (prev.includes(part)) {
        return prev.length > 1 ? prev.filter((p) => p !== part) : prev
      }
      return [...prev, part].sort()
    })
  }

  const launchTest = async (key, payload) => {
    setError('')
    setLaunching(key)
    try {
      const res = await api.createTest(payload)
      navigate(`/test/${res.test.id}`)
    } catch (err) {
      setError(err.message || 'Could not start test')
    } finally {
      setLaunching('')
    }
  }

  const totals = meta?.counts?.totals || { pyq: 0, ai: 0, total: 0 }
  const userStats = meta?.user_stats || { mistake_count: 0, bookmark_count: 0 }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 transition-colors">
      {/* Top Navbar */}
      <header className="sticky top-0 z-30 border-b border-slate-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-sm">
              <FlaskConical className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-base sm:text-lg font-bold tracking-tight text-slate-900 dark:text-white leading-tight">
                CSIR NET Life Sciences CBT
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 hidden sm:block">
                NTA Exam Simulator · PYQs & Verified AI Bank
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            <div className="hidden sm:flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/60 px-3 py-1.5">
              <span className="text-xs font-medium text-slate-700 dark:text-slate-200">
                {user?.display_name || user?.username}
              </span>
              <span className="rounded-md bg-indigo-100 dark:bg-indigo-950 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
                {user?.role}
              </span>
            </div>
            <ThemeToggle />
            <button
              type="button"
              onClick={logout}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <LogOut className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Logout</span>
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">
        {error && (
          <div className="flex items-center gap-2.5 rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/50 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Top Summary Cards */}
        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            icon={<CheckCircle2 className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />}
            label="Tests Completed"
            value={loading ? '—' : String(analytics?.total_tests ?? 0)}
            sub={
              analytics?.in_progress_tests
                ? `${analytics.in_progress_tests} in progress`
                : 'Ready to practice'
            }
          />
          <StatCard
            icon={<Award className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />}
            label="Average Score"
            value={loading ? '—' : `${analytics?.average_score ?? 0}`}
            sub="Marks across completed tests"
          />
          <StatCard
            icon={<Target className="h-5 w-5 text-amber-600 dark:text-amber-400" />}
            label="Overall Accuracy"
            value={loading ? '—' : `${analytics?.overall_accuracy ?? 0}%`}
            sub={
              analytics
                ? `${analytics.total_correct} correct · ${analytics.total_incorrect} wrong`
                : ''
            }
          />
          <StatCard
            icon={<Database className="h-5 w-5 text-cyan-600 dark:text-cyan-400" />}
            label="Active Question Bank"
            value={loading ? '—' : totals.total.toLocaleString()}
            sub={`${totals.pyq.toLocaleString()} PYQs + ${totals.ai.toLocaleString()} Verified AI`}
          />
        </section>

        {/* Quick-Launch Builders + Unit Mastery */}
        <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left Column: Full Mock + Mistake/Bookmark Drills */}
          <div className="lg:col-span-5 space-y-6">
            {/* Full-Length Mock Card */}
            <div className="rounded-2xl border border-indigo-200 dark:border-indigo-900/60 bg-gradient-to-br from-white to-indigo-50/50 dark:from-slate-900 dark:to-indigo-950/30 p-6 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-100 dark:bg-indigo-950 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300">
                    <Flame className="h-3.5 w-3.5" />
                    NTA Pattern Exam
                  </span>
                  <h2 className="text-lg font-bold text-slate-900 dark:text-white mt-2">
                    Full-Length Mock Test
                  </h2>
                  <p className="text-xs text-slate-600 dark:text-slate-300 mt-1">
                    145 Qs (20 A · 50 B · 75 C) · 180 min · 200 Marks · First-N Grading
                  </p>
                </div>
              </div>

              <div className="mt-4">
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
                  Question Source Mix
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {SOURCE_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setFullMockSource(opt.value)}
                      className={`rounded-xl px-2.5 py-2 text-xs font-medium border transition-colors ${
                        fullMockSource === opt.value
                          ? 'border-indigo-600 bg-indigo-600 text-white shadow-sm'
                          : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between rounded-xl border border-indigo-200/80 dark:border-indigo-900/60 bg-white/80 dark:bg-slate-900/60 px-3.5 py-2.5">
                <div className="flex items-center gap-2">
                  <Calculator className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
                  <span className="text-xs font-medium text-slate-700 dark:text-slate-200">
                    Enable Virtual Calculator
                  </span>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enableCalculator}
                  onClick={toggleCalculatorPref}
                  className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                    enableCalculator ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-700'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform mt-0.5 ${
                      enableCalculator ? 'translate-x-4 ml-0.5' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>

              <button
                type="button"
                disabled={launching !== ''}
                onClick={() =>
                  launchTest('full_mock', {
                    test_type: 'full_mock',
                    source_filter: fullMockSource,
                    instant_feedback: false,
                  })
                }
                className="mt-5 w-full inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-4 py-3 text-sm font-semibold text-white shadow-sm transition-colors"
              >
                <Play className="h-4 w-4 fill-current" />
                <span>
                  {launching === 'full_mock' ? 'Generating Paper...' : 'Start Full Mock Test'}
                </span>
              </button>
            </div>

            {/* Mistake Drill & Bookmark Drill */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="rounded-lg bg-rose-100 dark:bg-rose-950/60 p-2 text-rose-600 dark:text-rose-400">
                      <AlertCircle className="h-4 w-4" />
                    </span>
                    <span className="text-xs font-bold font-mono text-rose-600 dark:text-rose-400">
                      {userStats.mistake_count} Qs
                    </span>
                  </div>
                  <h3 className="font-bold text-slate-900 dark:text-white mt-3">
                    Mistake Drill
                  </h3>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    Retry questions you previously answered incorrectly.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={userStats.mistake_count === 0 || launching !== ''}
                  onClick={() =>
                    launchTest('mistake_drill', {
                      test_type: 'mistake_drill',
                      instant_feedback: true,
                    })
                  }
                  className="mt-4 w-full rounded-xl border border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/40 hover:bg-rose-100 dark:hover:bg-rose-900/50 disabled:opacity-45 disabled:cursor-not-allowed px-3 py-2 text-xs font-semibold text-rose-700 dark:text-rose-300 transition-colors"
                >
                  {launching === 'mistake_drill' ? 'Starting...' : 'Start Mistake Drill'}
                </button>
              </div>

              <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="rounded-lg bg-amber-100 dark:bg-amber-950/60 p-2 text-amber-600 dark:text-amber-400">
                      <Bookmark className="h-4 w-4" />
                    </span>
                    <span className="text-xs font-bold font-mono text-amber-600 dark:text-amber-400">
                      {userStats.bookmark_count} Qs
                    </span>
                  </div>
                  <h3 className="font-bold text-slate-900 dark:text-white mt-3">
                    Bookmark Drill
                  </h3>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    Practice questions you saved for revision.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={userStats.bookmark_count === 0 || launching !== ''}
                  onClick={() =>
                    launchTest('bookmark_drill', {
                      test_type: 'bookmark_drill',
                      instant_feedback: true,
                    })
                  }
                  className="mt-4 w-full rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/40 hover:bg-amber-100 dark:hover:bg-amber-900/50 disabled:opacity-45 disabled:cursor-not-allowed px-3 py-2 text-xs font-semibold text-amber-700 dark:text-amber-300 transition-colors"
                >
                  {launching === 'bookmark_drill' ? 'Starting...' : 'Start Bookmark Drill'}
                </button>
              </div>
            </div>
          </div>

          {/* Right Column: Custom Unit Practice Builder */}
          <div className="lg:col-span-7 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-sm flex flex-col justify-between">
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <SlidersHorizontal className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
                  <h2 className="text-lg font-bold text-slate-900 dark:text-white">
                    Custom Unit Practice
                  </h2>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => setSelectedUnits(meta?.syllabus_units || [])}
                    className="text-indigo-600 dark:text-indigo-400 hover:underline font-medium"
                  >
                    Select All
                  </button>
                  <span className="text-slate-300 dark:text-slate-700">|</span>
                  <button
                    type="button"
                    onClick={() => setSelectedUnits([])}
                    className="text-slate-500 dark:text-slate-400 hover:underline font-medium"
                  >
                    Clear ({selectedUnits.length === 0 ? 'All Units' : `${selectedUnits.length} selected`})
                  </button>
                </div>
              </div>

              {/* Multi-select Syllabus Units */}
              <div className="max-h-52 overflow-y-auto rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-950/50 p-2.5 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {(meta?.counts?.by_unit || []).map((u) => {
                  const checked = selectedUnits.includes(u.syllabus_unit)
                  return (
                    <button
                      key={u.syllabus_unit}
                      type="button"
                      onClick={() => toggleUnit(u.syllabus_unit)}
                      className={`text-left rounded-lg px-2.5 py-2 border text-xs transition-colors flex items-center justify-between gap-2 ${
                        checked
                          ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/60 text-indigo-900 dark:text-indigo-200 font-medium'
                          : 'border-transparent hover:bg-white dark:hover:bg-slate-800/70 text-slate-700 dark:text-slate-300'
                      }`}
                    >
                      <span className="truncate">{u.syllabus_unit}</span>
                      <span className="shrink-0 font-mono text-[10px] text-slate-400 dark:text-slate-500">
                        {u.total_count}
                      </span>
                    </button>
                  )
                })}
              </div>

              {/* Parts, Question Count, Source Filter */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5">
                    Parts
                  </label>
                  <div className="flex gap-1.5">
                    {['A', 'B', 'C'].map((p) => {
                      const active = selectedParts.includes(p)
                      return (
                        <button
                          key={p}
                          type="button"
                          onClick={() => togglePart(p)}
                          className={`flex-1 rounded-lg py-2 text-xs font-semibold border transition-colors ${
                            active
                              ? 'border-indigo-600 bg-indigo-600 text-white'
                              : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300'
                          }`}
                        >
                          Part {p}
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5">
                    Questions: <span className="text-indigo-600 dark:text-indigo-400 font-mono">{questionCount}</span>
                  </label>
                  <input
                    type="range"
                    min={10}
                    max={100}
                    step={5}
                    value={questionCount}
                    onChange={(e) => setQuestionCount(Number(e.target.value))}
                    className="w-full accent-indigo-600 mt-2"
                  />
                  <div className="flex justify-between text-[10px] text-slate-400 font-mono">
                    <span>10</span>
                    <span>50</span>
                    <span>100</span>
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5">
                    Source Filter
                  </label>
                  <select
                    value={practiceSource}
                    onChange={(e) => setPracticeSource(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    {SOURCE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Timer & Instant Feedback */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
                <div className="flex items-center justify-between rounded-xl border border-slate-200 dark:border-slate-800 px-3.5 py-2.5">
                  <div className="flex items-center gap-2">
                    <Clock className="h-4 w-4 text-slate-500" />
                    <span className="text-xs font-medium text-slate-700 dark:text-slate-200">
                      Timer
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {useTimer && (
                      <input
                        type="number"
                        min={5}
                        max={240}
                        value={durationMinutes}
                        onChange={(e) =>
                          setDurationMinutes(Math.max(5, Math.min(240, Number(e.target.value) || 30)))
                        }
                        className="w-16 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs font-mono text-center"
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => setUseTimer((v) => !v)}
                      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                        useTimer ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-700'
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform mt-0.5 ${
                          useTimer ? 'translate-x-4 ml-0.5' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-xl border border-slate-200 dark:border-slate-800 px-3.5 py-2.5">
                  <div>
                    <div className="text-xs font-medium text-slate-700 dark:text-slate-200">
                      Instant Feedback
                    </div>
                    <div className="text-[10px] text-slate-400">
                      {instantFeedback ? 'Reveal answer after each Q' : 'Grade on final submit'}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setInstantFeedback((v) => !v)}
                    className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                      instantFeedback ? 'bg-emerald-600' : 'bg-slate-300 dark:bg-slate-700'
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform mt-0.5 ${
                        instantFeedback ? 'translate-x-4 ml-0.5' : 'translate-x-0.5'
                      }`}
                    />
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between rounded-xl border border-slate-200 dark:border-slate-800 px-3.5 py-2.5">
                <div className="flex items-center gap-2">
                  <Calculator className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
                  <div>
                    <div className="text-xs font-medium text-slate-700 dark:text-slate-200">
                      Enable Virtual Calculator
                    </div>
                    <div className="text-[10px] text-slate-400">
                      {enableCalculator ? 'TCS iON scientific calculator shown in exam bar' : 'Hidden for mental-math practice'}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enableCalculator}
                  onClick={toggleCalculatorPref}
                  className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                    enableCalculator ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-700'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform mt-0.5 ${
                      enableCalculator ? 'translate-x-4 ml-0.5' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            </div>

            <button
              type="button"
              disabled={launching !== ''}
              onClick={() =>
                launchTest('unit_practice', {
                  test_type: 'unit_practice',
                  syllabus_units: selectedUnits.length > 0 ? selectedUnits : null,
                  parts: selectedParts,
                  question_count: questionCount,
                  source_filter: practiceSource,
                  duration_minutes: useTimer ? durationMinutes : null,
                  instant_feedback: instantFeedback,
                })
              }
              className="mt-5 w-full inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 dark:bg-indigo-600 hover:bg-slate-800 dark:hover:bg-indigo-700 disabled:opacity-50 px-4 py-3 text-sm font-semibold text-white transition-colors"
            >
              <BookOpen className="h-4 w-4" />
              <span>
                {launching === 'unit_practice'
                  ? 'Building Practice Set...'
                  : 'Start Custom Unit Practice'}
              </span>
            </button>
          </div>
        </section>

        {/* Unit-Wise Mastery Bar Chart */}
        <section className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
            <div className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
              <div>
                <h2 className="text-base font-bold text-slate-900 dark:text-white">
                  Syllabus Unit Mastery
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Sorted by weakest accuracy across CSIR Life Sciences & DBT units
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-rose-500" /> &lt;50% Weak
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-amber-500" /> 50–74%
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> ≥75% Strong
              </span>
            </div>
          </div>

          {unitMasteryData.some((d) => d.attempted) ? (
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={unitMasteryData}
                  margin={{ top: 10, right: 16, left: -12, bottom: 50 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke={theme === 'dark' ? '#1e293b' : '#e2e8f0'}
                    vertical={false}
                  />
                  <XAxis
                    dataKey="shortLabel"
                    angle={-30}
                    textAnchor="end"
                    interval={0}
                    height={68}
                    tick={{
                      fontSize: 10,
                      fill: theme === 'dark' ? '#94a3b8' : '#475569',
                    }}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                    tick={{
                      fontSize: 11,
                      fill: theme === 'dark' ? '#94a3b8' : '#475569',
                    }}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: theme === 'dark' ? '#0f172a' : '#ffffff',
                      borderColor: theme === 'dark' ? '#334155' : '#e2e8f0',
                      borderRadius: '12px',
                      fontSize: '12px',
                    }}
                    formatter={(val, _name, item) => [
                      item.payload.attempted
                        ? `${val}% (${item.payload.correct}/${item.payload.evaluated})`
                        : 'Not attempted yet',
                      'Accuracy',
                    ]}
                    labelFormatter={(_label, payload) =>
                      payload?.[0]?.payload?.syllabus_unit || ''
                    }
                  />
                  <Bar dataKey="accuracy" radius={[6, 6, 0, 0]} maxBarSize={34}>
                    {unitMasteryData.map((entry) => {
                      let fill = theme === 'dark' ? '#334155' : '#cbd5e1'
                      if (entry.attempted) {
                        fill =
                          entry.accuracy < 50
                            ? '#f43f5e'
                            : entry.accuracy < 75
                              ? '#f59e0b'
                              : '#10b981'
                      }
                      return <Cell key={entry.syllabus_unit} fill={fill} />
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 py-10 text-center text-sm text-slate-500 dark:text-slate-400">
              Complete a mock test or practice session to visualize your unit-wise accuracy and identify weak topics.
            </div>
          )}
        </section>

        {/* Recent Test History */}
        <section className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Recent Test History
            </h2>
            <span className="text-xs text-slate-500 dark:text-slate-400">
              {analytics?.recent_tests?.length || 0} sessions
            </span>
          </div>

          {analytics?.recent_tests?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 dark:bg-slate-950/60 text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">
                  <tr>
                    <th className="px-6 py-3 font-semibold">Test</th>
                    <th className="px-4 py-3 font-semibold">Mode / Source</th>
                    <th className="px-4 py-3 font-semibold">Score</th>
                    <th className="px-4 py-3 font-semibold">Accuracy</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                    <th className="px-4 py-3 font-semibold">Started</th>
                    <th className="px-6 py-3 font-semibold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {analytics.recent_tests.map((t) => (
                    <tr
                      key={t.id}
                      className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors"
                    >
                      <td className="px-6 py-3.5 font-medium text-slate-900 dark:text-slate-100">
                        <div>{t.title}</div>
                        <div className="text-xs text-slate-400 font-normal">
                          {t.total_questions} Questions
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-xs text-slate-600 dark:text-slate-300 capitalize">
                        {t.test_type.replace('_', ' ')} · {t.source_filter.replace('_', ' ')}
                      </td>
                      <td className="px-4 py-3.5 font-mono text-xs font-semibold">
                        {t.status === 'completed'
                          ? `${t.total_score} / ${t.max_score}`
                          : '—'}
                      </td>
                      <td className="px-4 py-3.5 font-mono text-xs">
                        {t.status === 'completed' && t.accuracy !== null
                          ? `${t.accuracy}%`
                          : '—'}
                      </td>
                      <td className="px-4 py-3.5">
                        {t.status === 'in_progress' ? (
                          <span className="inline-flex items-center rounded-full bg-amber-100 dark:bg-amber-950/70 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:text-amber-300">
                            In Progress
                          </span>
                        ) : (
                          <span className="inline-flex items-center rounded-full bg-emerald-100 dark:bg-emerald-950/70 px-2.5 py-0.5 text-xs font-medium text-emerald-800 dark:text-emerald-300">
                            Submitted
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3.5 text-xs text-slate-500 dark:text-slate-400">
                        {formatDate(t.started_at)}
                      </td>
                      <td className="px-6 py-3.5 text-right">
                        {t.status === 'in_progress' ? (
                          <button
                            type="button"
                            onClick={() => navigate(`/test/${t.id}`)}
                            className="rounded-lg bg-amber-500 hover:bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors"
                          >
                            Resume
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => navigate(`/review/${t.id}`)}
                            className="rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/50 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 px-3 py-1.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300 transition-colors"
                          >
                            View Scorecard & Review
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-slate-500 dark:text-slate-400">
              No tests taken yet. Start a Full Mock Test or Custom Unit Practice above!
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

function StatCard({ icon, label, value, sub }) {
  return (
    <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          {label}
        </span>
        <div className="rounded-xl bg-slate-100 dark:bg-slate-800 p-2">{icon}</div>
      </div>
      <div className="mt-3 text-2xl font-bold font-mono text-slate-900 dark:text-white">
        {value}
      </div>
      {sub && (
        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{sub}</div>
      )}
    </div>
  )
}
