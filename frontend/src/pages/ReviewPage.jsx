import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Award,
  Bookmark,
  CheckCircle2,
  Clock,
  Flag,
  Flame,
  MinusCircle,
  Target,
  TrendingDown,
  XCircle,
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
import FlagModal from '../components/FlagModal'
import MarkdownMath from '../components/MarkdownMath'
import ThemeToggle from '../components/ThemeToggle'
import { useTheme } from '../context/ThemeContext'

function formatDuration(totalSec) {
  const s = Math.max(0, Math.floor(totalSec || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h ${m}m ${sec}s`
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

function shortenUnit(unit) {
  if (!unit) return ''
  if (unit === 'General Aptitude') return 'Aptitude'
  const m = unit.match(/^(Unit\s+\d+):\s*(.+)$/i)
  if (!m) return unit.slice(0, 18)
  const title = m[2].length > 16 ? `${m[2].slice(0, 14)}…` : m[2]
  return `${m[1]} (${title})`
}

function isAnswered(q) {
  return (q.status === 'answered' || q.status === 'answered_marked') && Boolean(q.selected_option)
}

export default function ReviewPage() {
  const { id } = useParams()
  const testId = Number(id)
  const navigate = useNavigate()
  const { theme } = useTheme()

  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [flagTarget, setFlagTarget] = useState(null)
  const [toast, setToast] = useState('')

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const res = await api.getTest(testId)
        if (!active) return
        if (res.test.status === 'in_progress') {
          navigate(`/test/${testId}`, { replace: true })
          return
        }
        setData(res)
      } catch (err) {
        if (active) setError(err.message || 'Failed to load scorecard')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => {
      active = false
    }
  }, [testId, navigate])

  const questions = data?.questions || []
  const scorecard = data?.scorecard || {}
  const test = data?.test

  const totalTimeSec = useMemo(
    () => questions.reduce((acc, q) => acc + (q.time_spent_sec || 0), 0),
    [questions]
  )

  const slowQuestions = useMemo(
    () =>
      questions
        .filter((q) => (q.time_spent_sec || 0) > 180)
        .sort((a, b) => (b.time_spent_sec || 0) - (a.time_spent_sec || 0)),
    [questions]
  )

  const filterCounts = useMemo(() => {
    return {
      all: questions.length,
      incorrect: questions.filter((q) => q.is_evaluated && q.is_correct === false).length,
      correct: questions.filter((q) => q.is_evaluated && q.is_correct === true).length,
      unattempted: questions.filter((q) => !isAnswered(q)).length,
      over_limit: questions.filter((q) => isAnswered(q) && q.is_evaluated === false).length,
      marked: questions.filter(
        (q) => q.status === 'marked' || q.status === 'answered_marked'
      ).length,
    }
  }, [questions])

  const filteredQuestions = useMemo(() => {
    switch (filter) {
      case 'incorrect':
        return questions.filter((q) => q.is_evaluated && q.is_correct === false)
      case 'correct':
        return questions.filter((q) => q.is_evaluated && q.is_correct === true)
      case 'unattempted':
        return questions.filter((q) => !isAnswered(q))
      case 'over_limit':
        return questions.filter((q) => isAnswered(q) && q.is_evaluated === false)
      case 'marked':
        return questions.filter(
          (q) => q.status === 'marked' || q.status === 'answered_marked'
        )
      default:
        return questions
    }
  }, [questions, filter])

  const unitChartData = useMemo(
    () =>
      (scorecard.unit_summary || []).map((u) => ({
        ...u,
        shortLabel: shortenUnit(u.syllabus_unit),
      })),
    [scorecard]
  )

  const handleToggleBookmark = async (q) => {
    try {
      const res = await api.toggleBookmark({ response_id: q.response_id })
      setData((prev) => ({
        ...prev,
        questions: prev.questions.map((item) =>
          item.response_id === q.response_id
            ? { ...item, is_bookmarked: res.bookmarked }
            : item
        ),
      }))
      setToast(res.bookmarked ? 'Bookmarked' : 'Bookmark removed')
      setTimeout(() => setToast(''), 2000)
    } catch {
      // ignore
    }
  }

  const scrollToQuestion = (order) => {
    setFilter('all')
    setTimeout(() => {
      const el = document.getElementById(`review-q-${order}`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 50)
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-950 text-slate-600 dark:text-slate-300">
        Loading scorecard and solution review...
      </div>
    )
  }

  if (error || !test) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-slate-50 dark:bg-slate-950 p-6 text-center">
        <p className="text-rose-600 dark:text-rose-400 font-medium mb-4">
          {error || 'Scorecard not found'}
        </p>
        <button
          type="button"
          onClick={() => navigate('/')}
          className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white"
        >
          Back to Dashboard
        </button>
      </div>
    )
  }

  const FILTER_TABS = [
    { key: 'all', label: 'All' },
    { key: 'incorrect', label: 'Incorrect' },
    { key: 'correct', label: 'Correct' },
    { key: 'unattempted', label: 'Unattempted' },
    { key: 'over_limit', label: 'Ignored Over-Limit' },
    { key: 'marked', label: 'Marked' },
  ]

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 transition-colors">
      {/* Sticky Header */}
      <header className="sticky top-0 z-30 border-b border-slate-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() => navigate('/')}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-xs font-semibold hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>Dashboard</span>
            </button>
            <div className="min-w-0">
              <h1 className="text-sm sm:text-base font-bold truncate text-slate-900 dark:text-white">
                {test.title} — Scorecard & Review
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {test.total_questions} Questions · {test.source_filter.replace('_', ' ')}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            {toast && (
              <span className="text-xs font-medium text-indigo-600 dark:text-indigo-400">
                {toast}
              </span>
            )}
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Hero Score Card */}
        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="rounded-2xl border border-indigo-200 dark:border-indigo-900 bg-gradient-to-br from-indigo-600 to-indigo-700 text-white p-5 shadow-md">
            <div className="flex items-center justify-between text-indigo-100 text-xs font-semibold uppercase tracking-wider">
              <span>Total Marks</span>
              <Award className="h-5 w-5" />
            </div>
            <div className="mt-3 text-3xl font-bold font-mono">
              {scorecard.total_score ?? test.total_score}{' '}
              <span className="text-base font-normal text-indigo-200">
                / {scorecard.max_score ?? test.max_score}
              </span>
            </div>
            <div className="mt-1 text-xs text-indigo-100">
              {scorecard.correct_count ?? 0} correct · {scorecard.incorrect_count ?? 0} wrong
            </div>
          </div>

          <HeroStat
            icon={<Target className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />}
            label="Accuracy"
            value={`${scorecard.accuracy ?? 0}%`}
            sub={`${scorecard.unanswered_count ?? 0} unattempted`}
          />
          <HeroStat
            icon={<TrendingDown className="h-5 w-5 text-rose-600 dark:text-rose-400" />}
            label="Negative Marks Lost"
            value={`-${scorecard.negative_loss ?? 0}`}
            sub={
              scorecard.over_limit_count
                ? `${scorecard.over_limit_count} over-limit Qs ignored`
                : 'Within section limits'
            }
          />
          <HeroStat
            icon={<Clock className="h-5 w-5 text-amber-600 dark:text-amber-400" />}
            label="Time Taken"
            value={formatDuration(totalTimeSec)}
            sub={`${slowQuestions.length} questions > 3 min`}
          />
        </section>

        {/* Part-wise Breakdown Table */}
        <section className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-800">
            <h2 className="text-base font-bold text-slate-900 dark:text-white">
              Part-Wise Performance Breakdown
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 dark:bg-slate-950/60 text-xs uppercase tracking-wider text-slate-500 dark:text-slate-400">
                <tr>
                  <th className="px-6 py-3">Part</th>
                  <th className="px-4 py-3">Attempted</th>
                  <th className="px-4 py-3">Evaluated</th>
                  <th className="px-4 py-3">Ignored Over-Limit</th>
                  <th className="px-4 py-3">Correct</th>
                  <th className="px-4 py-3">Wrong</th>
                  <th className="px-4 py-3">Accuracy</th>
                  <th className="px-6 py-3 text-right">Marks</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {(scorecard.part_summary || []).map((p) => (
                  <tr key={p.part}>
                    <td className="px-6 py-3 font-bold">Part {p.part}</td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {p.attempted} / {p.total_questions}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {p.evaluated} (max {p.max_evaluated_allowed})
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {p.over_limit_ignored > 0 ? (
                        <span className="rounded bg-amber-100 dark:bg-amber-950 px-2 py-0.5 text-amber-800 dark:text-amber-300 font-semibold">
                          {p.over_limit_ignored}
                        </span>
                      ) : (
                        '0'
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-emerald-600 dark:text-emerald-400 font-semibold">
                      {p.correct}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-rose-600 dark:text-rose-400 font-semibold">
                      {p.incorrect}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{p.accuracy}%</td>
                    <td className="px-6 py-3 font-mono text-xs font-bold text-right">
                      {p.score} / {p.max_score}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Unit-wise Accuracy Chart + >180s Time Highlights */}
        <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-8 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-sm">
            <h2 className="text-base font-bold text-slate-900 dark:text-white mb-1">
              Unit-Wise Accuracy
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
              Performance breakdown across syllabus units in this test
            </p>
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={unitChartData}
                  margin={{ top: 10, right: 12, left: -14, bottom: 48 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke={theme === 'dark' ? '#1e293b' : '#e2e8f0'}
                    vertical={false}
                  />
                  <XAxis
                    dataKey="shortLabel"
                    angle={-25}
                    textAnchor="end"
                    interval={0}
                    height={60}
                    tick={{ fontSize: 10, fill: theme === 'dark' ? '#94a3b8' : '#475569' }}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                    tick={{ fontSize: 11, fill: theme === 'dark' ? '#94a3b8' : '#475569' }}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: theme === 'dark' ? '#0f172a' : '#ffffff',
                      borderColor: theme === 'dark' ? '#334155' : '#e2e8f0',
                      borderRadius: '12px',
                      fontSize: '12px',
                    }}
                    formatter={(val, _n, item) => [
                      `${val}% (${item.payload.correct}/${item.payload.evaluated} evaluated)`,
                      'Accuracy',
                    ]}
                    labelFormatter={(_l, p) => p?.[0]?.payload?.syllabus_unit || ''}
                  />
                  <Bar dataKey="accuracy" radius={[6, 6, 0, 0]} maxBarSize={36}>
                    {unitChartData.map((entry) => (
                      <Cell
                        key={entry.syllabus_unit}
                        fill={
                          entry.evaluated === 0
                            ? theme === 'dark'
                              ? '#334155'
                              : '#cbd5e1'
                            : entry.accuracy < 50
                              ? '#f43f5e'
                              : entry.accuracy < 75
                                ? '#f59e0b'
                                : '#10b981'
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Time-per-question highlights (>180s) */}
          <div className="lg:col-span-4 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-sm flex flex-col">
            <div className="flex items-center gap-2 mb-1">
              <Flame className="h-4 w-4 text-amber-500" />
              <h2 className="text-base font-bold text-slate-900 dark:text-white">
                Time Sink Highlights (&gt;180s)
              </h2>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
              Questions where you spent over 3 minutes
            </p>

            {slowQuestions.length > 0 ? (
              <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                {slowQuestions.map((q) => (
                  <button
                    key={q.response_id}
                    type="button"
                    onClick={() => scrollToQuestion(q.question_order)}
                    className="w-full text-left rounded-xl border border-amber-200 dark:border-amber-900/60 bg-amber-50/70 dark:bg-amber-950/30 hover:bg-amber-100 dark:hover:bg-amber-900/40 px-3.5 py-2.5 flex items-center justify-between gap-2 transition-colors"
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-bold text-slate-900 dark:text-white">
                        Q.{q.question_order} · Part {q.part}
                      </div>
                      <div className="text-[11px] text-slate-500 dark:text-slate-400 truncate">
                        {q.syllabus_unit}
                      </div>
                    </div>
                    <span className="shrink-0 font-mono text-xs font-bold text-amber-700 dark:text-amber-300">
                      {formatDuration(q.time_spent_sec)}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-6 text-center text-xs text-slate-500 dark:text-slate-400">
                Great pacing! No question exceeded 180 seconds.
              </div>
            )}
          </div>
        </section>

        {/* Filterable Question-by-Question Review */}
        <section className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold text-slate-900 dark:text-white">
              Question-by-Question Solution Review
            </h2>

            <div className="flex flex-wrap gap-1.5">
              {FILTER_TABS.map((tab) => {
                const active = filter === tab.key
                return (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => setFilter(tab.key)}
                    className={`rounded-xl px-3 py-1.5 text-xs font-semibold border transition-colors ${
                      active
                        ? 'border-indigo-600 bg-indigo-600 text-white'
                        : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'
                    }`}
                  >
                    {tab.label}{' '}
                    <span className="font-mono opacity-80">({filterCounts[tab.key]})</span>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="space-y-4">
            {filteredQuestions.map((q) => {
              const answered = isAnswered(q)
              const ignoredOverLimit = answered && q.is_evaluated === false
              const slow = (q.time_spent_sec || 0) > 180

              return (
                <article
                  key={q.response_id}
                  id={`review-q-${q.question_order}`}
                  className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden"
                >
                  {/* Card Header */}
                  <div className="px-5 py-3.5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-bold text-slate-900 dark:text-white">
                        Q.{q.question_order}
                      </span>
                      <span className="rounded-md bg-slate-200/80 dark:bg-slate-800 px-2 py-0.5 text-xs font-semibold">
                        Part {q.part}
                      </span>
                      <span className="rounded-md bg-indigo-50 dark:bg-indigo-950/60 px-2 py-0.5 text-xs text-indigo-700 dark:text-indigo-300">
                        {q.syllabus_unit}
                      </span>
                      {q.source_label && (
                        <span className="rounded-md bg-cyan-100 dark:bg-cyan-950/70 border border-cyan-200 dark:border-cyan-800 px-2 py-0.5 text-xs font-semibold text-cyan-800 dark:text-cyan-300">
                          {q.source_label}
                        </span>
                      )}
                      {slow && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 dark:bg-amber-950/70 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:text-amber-300">
                          <Clock className="h-3 w-3" />
                          {formatDuration(q.time_spent_sec)}
                        </span>
                      )}
                      {!slow && (
                        <span className="text-xs font-mono text-slate-400">
                          {formatDuration(q.time_spent_sec)}
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2">
                      {/* Verdict / Marks Pill */}
                      {ignoredOverLimit ? (
                        <span className="rounded-full bg-amber-100 dark:bg-amber-950 px-2.5 py-0.5 text-xs font-semibold text-amber-800 dark:text-amber-300">
                          Ignored Over-Limit (0)
                        </span>
                      ) : !answered ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 dark:bg-slate-800 px-2.5 py-0.5 text-xs font-medium text-slate-500">
                          <MinusCircle className="h-3.5 w-3.5" /> Unattempted (0)
                        </span>
                      ) : q.is_correct ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 dark:bg-emerald-950/70 px-2.5 py-0.5 text-xs font-bold text-emerald-700 dark:text-emerald-300">
                          <CheckCircle2 className="h-3.5 w-3.5" /> +{q.marks_awarded}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 dark:bg-rose-950/70 px-2.5 py-0.5 text-xs font-bold text-rose-700 dark:text-rose-300">
                          <XCircle className="h-3.5 w-3.5" /> {q.marks_awarded}
                        </span>
                      )}

                      <button
                        type="button"
                        onClick={() => handleToggleBookmark(q)}
                        className={`rounded-lg border p-1.5 transition-colors ${
                          q.is_bookmarked
                            ? 'border-amber-400 bg-amber-50 dark:bg-amber-950/50 text-amber-600'
                            : 'border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800'
                        }`}
                        title="Toggle Bookmark"
                      >
                        <Bookmark
                          className={`h-3.5 w-3.5 ${q.is_bookmarked ? 'fill-current' : ''}`}
                        />
                      </button>

                      <button
                        type="button"
                        onClick={() => setFlagTarget({ response_id: q.response_id })}
                        className="rounded-lg border border-slate-200 dark:border-slate-700 p-1.5 text-slate-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
                        title="Flag Question"
                      >
                        <Flag className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Body */}
                  <div className="p-5 space-y-4">
                    <div className="text-sm sm:text-base">
                      <MarkdownMath content={q.question_text} />
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      {['A', 'B', 'C', 'D'].map((optKey) => {
                        const optText = q.options?.[optKey]
                        if (optText === undefined) return null
                        const isCorrect = q.correct_option === optKey
                        const isUserChoice = q.selected_option === optKey

                        let cls =
                          'border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-950/40'
                        if (isCorrect) {
                          cls =
                            'border-emerald-500 bg-emerald-50 dark:bg-emerald-950/40 ring-1 ring-emerald-500'
                        } else if (isUserChoice && !isCorrect) {
                          cls =
                            'border-rose-500 bg-rose-50 dark:bg-rose-950/40 ring-1 ring-rose-500'
                        }

                        return (
                          <div
                            key={optKey}
                            className={`rounded-xl border p-3 flex items-start gap-2.5 text-sm ${cls}`}
                          >
                            <span
                              className={`h-6 w-6 shrink-0 rounded-full flex items-center justify-center text-xs font-bold border ${
                                isCorrect
                                  ? 'bg-emerald-600 border-emerald-600 text-white'
                                  : isUserChoice
                                    ? 'bg-rose-600 border-rose-600 text-white'
                                    : 'border-slate-300 dark:border-slate-700 text-slate-500'
                              }`}
                            >
                              {optKey}
                            </span>
                            <div className="flex-1 pt-0.5">
                              <MarkdownMath content={optText} inline />
                            </div>
                          </div>
                        )
                      })}
                    </div>

                    {/* User choice vs Correct + Explanation */}
                    <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950/50 p-4 space-y-2">
                      <div className="flex flex-wrap items-center gap-4 text-xs font-semibold">
                        <span>
                          Your Choice:{' '}
                          <span className="font-mono text-slate-900 dark:text-white">
                            {q.selected_option || 'None'}
                          </span>
                        </span>
                        <span>
                          Correct Answer:{' '}
                          <span className="font-mono text-emerald-600 dark:text-emerald-400">
                            {q.correct_option}
                          </span>
                        </span>
                      </div>
                      {q.explanation ? (
                        <div className="text-sm pt-2 border-t border-slate-200 dark:border-slate-800">
                          <MarkdownMath content={q.explanation} />
                        </div>
                      ) : (
                        <p className="text-xs text-slate-500 dark:text-slate-400 italic">
                          Official PYQ — answer verified from exam key.
                        </p>
                      )}
                    </div>
                  </div>
                </article>
              )
            })}

            {filteredQuestions.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 dark:border-slate-800 p-10 text-center text-sm text-slate-500 dark:text-slate-400">
                No questions match the selected filter.
              </div>
            )}
          </div>
        </section>
      </main>

      <FlagModal
        isOpen={Boolean(flagTarget)}
        onClose={() => setFlagTarget(null)}
        target={flagTarget}
        onFlagged={() => {
          setToast('Question flagged')
          setTimeout(() => setToast(''), 2000)
        }}
      />
    </div>
  )
}

function HeroStat({ icon, label, value, sub }) {
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
