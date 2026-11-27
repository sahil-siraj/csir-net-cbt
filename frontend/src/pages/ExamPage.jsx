import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  Bookmark,
  Calculator,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Eraser,
  Flag,
  LayoutGrid,
  Send,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react'
import { api } from '../api/client'
import FlagModal from '../components/FlagModal'
import MarkdownMath from '../components/MarkdownMath'
import ScientificCalculator from '../components/ScientificCalculator'
import ThemeToggle from '../components/ThemeToggle'
import { createInitialCalculatorState } from '../utils/calculatorEngine'

const PART_LIMITS = { A: 15, B: 35, C: 25 }
const PART_MARKING = {
  A: '+2 / -0.5',
  B: '+2 / -0.5',
  C: '+4 / -1',
}

function parseUtcTimestamp(ts) {
  if (!ts) return Date.now()
  const iso = ts.includes('T') ? ts : ts.replace(' ', 'T') + 'Z'
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? Date.now() : ms
}

function formatCountdown(totalSec) {
  const s = Math.max(0, Math.floor(totalSec))
  const hrs = Math.floor(s / 3600)
  const mins = Math.floor((s % 3600) / 60)
  const secs = s % 60
  const pad = (n) => String(n).padStart(2, '0')
  return hrs > 0 ? `${pad(hrs)}:${pad(mins)}:${pad(secs)}` : `${pad(mins)}:${pad(secs)}`
}

function isQuestionAnswered(q) {
  return (
    (q.status === 'answered' || q.status === 'answered_marked') &&
    Boolean(q.selected_option)
  )
}

export default function ExamPage() {
  const { id } = useParams()
  const testId = Number(id)
  const navigate = useNavigate()

  const [test, setTest] = useState(null)
  const [questions, setQuestions] = useState([])
  const [currentIdx, setCurrentIdx] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [checking, setChecking] = useState(false)
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [mobilePaletteOpen, setMobilePaletteOpen] = useState(false)
  const [flagTarget, setFlagTarget] = useState(null)
  const [toast, setToast] = useState('')
  const [enableCalculator] = useState(() => {
    try {
      return localStorage.getItem('csir_enable_calculator') !== 'false'
    } catch {
      return true
    }
  })
  const [calcOpen, setCalcOpen] = useState(false)
  const [calcState, setCalcState] = useState(() => createInitialCalculatorState())

  // Reset calculator state whenever a new test starts
  useEffect(() => {
    setCalcOpen(false)
    setCalcState(createInitialCalculatorState())
  }, [testId])

  // Countdown / elapsed timer state
  const [remainingSec, setRemainingSec] = useState(null)
  const [elapsedStopwatchSec, setElapsedStopwatchSec] = useState(0)

  // Time tracking for current active question
  const activeEnteredAtRef = useRef(Date.now())
  const autoSubmittedRef = useRef(false)
  const questionsRef = useRef(questions)
  const currentIdxRef = useRef(currentIdx)
  questionsRef.current = questions
  currentIdxRef.current = currentIdx

  const showToast = useCallback((msg) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2600)
  }, [])

  // Load test on mount
  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      setError('')
      try {
        const data = await api.getTest(testId)
        if (!active) return
        if (data.test.status === 'completed') {
          navigate(`/review/${testId}`, { replace: true })
          return
        }
        const qs = data.questions || []
        // Mark first question as visited if still not_visited
        if (qs.length > 0 && qs[0].status === 'not_visited') {
          qs[0] = { ...qs[0], status: 'not_answered' }
          api
            .saveResponse(testId, {
              response_id: qs[0].response_id,
              selected_option: null,
              status: 'not_answered',
              time_spent_sec: qs[0].time_spent_sec || 0,
            })
            .catch(() => {})
        }
        setTest(data.test)
        setQuestions(qs)
        setCurrentIdx(0)
        activeEnteredAtRef.current = Date.now()

        if (data.test.duration_minutes) {
          const startedMs = parseUtcTimestamp(data.test.started_at)
          const totalDuration = data.test.duration_minutes * 60
          const alreadyElapsed = Math.max(0, Math.floor((Date.now() - startedMs) / 1000))
          setRemainingSec(Math.max(0, totalDuration - alreadyElapsed))
        } else {
          const startedMs = parseUtcTimestamp(data.test.started_at)
          setElapsedStopwatchSec(Math.max(0, Math.floor((Date.now() - startedMs) / 1000)))
        }
      } catch (err) {
        if (active) setError(err.message || 'Failed to load exam')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => {
      active = false
    }
  }, [testId, navigate])

  const handleFinalSubmit = useCallback(async () => {
    if (submitting) return
    setSubmitting(true)
    try {
      // Flush current question time before submitting
      const curQ = questionsRef.current[currentIdxRef.current]
      if (curQ) {
        const delta = Math.max(0, Math.floor((Date.now() - activeEnteredAtRef.current) / 1000))
        const totalTime = (curQ.time_spent_sec || 0) + delta
        const nextStatus = curQ.status === 'not_visited' ? 'not_answered' : curQ.status
        await api
          .saveResponse(testId, {
            response_id: curQ.response_id,
            selected_option: curQ.selected_option || null,
            status: nextStatus,
            time_spent_sec: totalTime,
          })
          .catch(() => {})
      }
      await api.submitTest(testId)
      navigate(`/review/${testId}`, { replace: true })
    } catch (err) {
      setError(err.message || 'Failed to submit test')
      setSubmitting(false)
      setShowSubmitModal(false)
    }
  }, [submitting, testId, navigate])

  // Countdown / stopwatch ticker
  useEffect(() => {
    if (!test) return
    const timer = setInterval(() => {
      if (test.duration_minutes) {
        setRemainingSec((prev) => {
          if (prev === null) return null
          const next = Math.max(0, prev - 1)
          if (next === 0 && !autoSubmittedRef.current) {
            autoSubmittedRef.current = true
            handleFinalSubmit()
          }
          return next
        })
      } else {
        setElapsedStopwatchSec((prev) => prev + 1)
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [test, handleFinalSubmit])

  // Periodic background flush of current question's elapsed time (every 15s)
  useEffect(() => {
    if (!test) return
    const interval = setInterval(() => {
      const idx = currentIdxRef.current
      const q = questionsRef.current[idx]
      if (!q) return
      const now = Date.now()
      const delta = Math.max(0, Math.floor((now - activeEnteredAtRef.current) / 1000))
      if (delta === 0) return
      activeEnteredAtRef.current = now
      const newTime = (q.time_spent_sec || 0) + delta
      const statusToSave = q.status === 'not_visited' ? 'not_answered' : q.status
      setQuestions((prev) =>
        prev.map((item, i) =>
          i === idx ? { ...item, time_spent_sec: newTime, status: statusToSave } : item
        )
      )
      api
        .saveResponse(testId, {
          response_id: q.response_id,
          selected_option: q.selected_option || null,
          status: statusToSave,
          time_spent_sec: newTime,
        })
        .catch(() => {})
    }, 15000)
    return () => clearInterval(interval)
  }, [test, testId])

  const persistQuestion = useCallback(
    (idx, patch = {}) => {
      const q = questionsRef.current[idx]
      if (!q) return
      const now = Date.now()
      const delta = Math.max(0, Math.floor((now - activeEnteredAtRef.current) / 1000))
      activeEnteredAtRef.current = now

      const newTime = (q.time_spent_sec || 0) + delta
      const nextOption =
        patch.selected_option !== undefined ? patch.selected_option : q.selected_option
      const nextStatus =
        patch.status !== undefined
          ? patch.status
          : q.status === 'not_visited'
            ? 'not_answered'
            : q.status

      setQuestions((prev) =>
        prev.map((item, i) =>
          i === idx
            ? {
                ...item,
                ...patch,
                selected_option: nextOption,
                status: nextStatus,
                time_spent_sec: newTime,
              }
            : item
        )
      )

      api
        .saveResponse(testId, {
          response_id: q.response_id,
          selected_option: nextOption || null,
          status: nextStatus,
          time_spent_sec: newTime,
        })
        .catch(() => {})
    },
    [testId]
  )

  const jumpToIndex = useCallback(
    (targetIdx, currentPatch = null) => {
      if (targetIdx < 0 || targetIdx >= questionsRef.current.length) return
      const curIdx = currentIdxRef.current
      if (curIdx !== targetIdx) {
        persistQuestion(curIdx, currentPatch || {})
      } else if (currentPatch) {
        persistQuestion(curIdx, currentPatch)
      }

      const targetQ = questionsRef.current[targetIdx]
      if (targetQ && targetQ.status === 'not_visited') {
        setQuestions((prev) =>
          prev.map((item, i) =>
            i === targetIdx ? { ...item, status: 'not_answered' } : item
          )
        )
        api
          .saveResponse(testId, {
            response_id: targetQ.response_id,
            selected_option: targetQ.selected_option || null,
            status: 'not_answered',
            time_spent_sec: targetQ.time_spent_sec || 0,
          })
          .catch(() => {})
      }
      activeEnteredAtRef.current = Date.now()
      setCurrentIdx(targetIdx)
      setMobilePaletteOpen(false)
    },
    [persistQuestion, testId]
  )

  const currentQuestion = questions[currentIdx] || null

  // Part summaries & counts
  const availableParts = useMemo(() => {
    const order = ['A', 'B', 'C']
    const set = new Set(questions.map((q) => q.part))
    return order.filter((p) => set.has(p))
  }, [questions])

  const partSummary = useMemo(() => {
    const map = {}
    for (const part of ['A', 'B', 'C']) {
      const partQs = questions.filter((q) => q.part === part)
      const answered = partQs.filter(isQuestionAnswered).length
      const limit = test?.test_type === 'full_mock' ? PART_LIMITS[part] : partQs.length
      map[part] = {
        total: partQs.length,
        answered,
        unanswered: partQs.length - answered,
        limit,
        overLimit: test?.test_type === 'full_mock' ? Math.max(0, answered - limit) : 0,
      }
    }
    return map
  }, [questions, test])

  const stateCounts = useMemo(() => {
    const counts = {
      not_visited: 0,
      not_answered: 0,
      answered: 0,
      marked: 0,
      answered_marked: 0,
    }
    for (const q of questions) {
      counts[q.status] = (counts[q.status] || 0) + 1
    }
    return counts
  }, [questions])

  // Determine which responses in a full_mock are beyond First-N limit (for visual hint)
  const overLimitResponseIds = useMemo(() => {
    const ids = new Set()
    if (test?.test_type !== 'full_mock') return ids
    for (const part of ['A', 'B', 'C']) {
      const limit = PART_LIMITS[part]
      const answeredInPart = questions
        .filter((q) => q.part === part && isQuestionAnswered(q))
        .sort((a, b) => a.question_order - b.question_order)
      for (const q of answeredInPart.slice(limit)) {
        ids.add(q.response_id)
      }
    }
    return ids
  }, [questions, test])

  const handleSelectOption = (opt) => {
    if (!currentQuestion || currentQuestion.is_checked) return
    const nextStatus =
      currentQuestion.status === 'marked' || currentQuestion.status === 'answered_marked'
        ? 'answered_marked'
        : 'answered'
    persistQuestion(currentIdx, {
      selected_option: opt,
      status: nextStatus,
    })
  }

  const handleClearResponse = () => {
    if (!currentQuestion || currentQuestion.is_checked) return
    persistQuestion(currentIdx, {
      selected_option: null,
      status: 'not_answered',
    })
  }

  const handleSaveAndNext = () => {
    if (!currentQuestion) return
    const nextStatus = currentQuestion.selected_option ? 'answered' : 'not_answered'
    const patch = {
      selected_option: currentQuestion.selected_option || null,
      status: nextStatus,
    }
    if (currentIdx + 1 < questions.length) {
      jumpToIndex(currentIdx + 1, patch)
    } else {
      persistQuestion(currentIdx, patch)
      showToast('Reached the last question')
    }
  }

  const handleMarkAndNext = () => {
    if (!currentQuestion) return
    const nextStatus = currentQuestion.selected_option ? 'answered_marked' : 'marked'
    const patch = {
      selected_option: currentQuestion.selected_option || null,
      status: nextStatus,
    }
    if (currentIdx + 1 < questions.length) {
      jumpToIndex(currentIdx + 1, patch)
    } else {
      persistQuestion(currentIdx, patch)
      showToast('Marked for review')
    }
  }

  const handleCheckAnswer = async () => {
    if (!currentQuestion || !test?.instant_feedback || !currentQuestion.selected_option) return
    setChecking(true)
    try {
      const res = await api.checkAnswer(testId, {
        response_id: currentQuestion.response_id,
        selected_option: currentQuestion.selected_option,
      })
      setQuestions((prev) =>
        prev.map((item, i) =>
          i === currentIdx
            ? {
                ...item,
                is_checked: true,
                status: item.status === 'answered_marked' ? 'answered_marked' : 'answered',
                correct_option: res.correct_option,
                explanation: res.explanation,
                is_correct: res.is_correct,
                question_source: res.question_source,
                exam_year: res.exam_year,
                source_label: res.source_label,
              }
            : item
        )
      )
    } catch (err) {
      showToast(err.message || 'Failed to check answer')
    } finally {
      setChecking(false)
    }
  }

  const handleToggleBookmark = async () => {
    if (!currentQuestion) return
    try {
      const res = await api.toggleBookmark({
        response_id: currentQuestion.response_id,
      })
      setQuestions((prev) =>
        prev.map((item, i) =>
          i === currentIdx ? { ...item, is_bookmarked: res.bookmarked } : item
        )
      )
      showToast(res.bookmarked ? 'Question bookmarked' : 'Bookmark removed')
    } catch (err) {
      showToast(err.message || 'Bookmark update failed')
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-950 text-slate-600 dark:text-slate-300">
        Loading exam workspace...
      </div>
    )
  }

  if (error || !test || !currentQuestion) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-slate-50 dark:bg-slate-950 p-6 text-center">
        <p className="text-rose-600 dark:text-rose-400 font-medium mb-4">
          {error || 'Exam could not be loaded'}
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

  const curPartStats = partSummary[currentQuestion.part]
  const showOverLimitBanner =
    test.test_type === 'full_mock' && curPartStats && curPartStats.overLimit > 0

  const timerColorClass =
    remainingSec !== null
      ? remainingSec <= 300
        ? 'bg-rose-100 dark:bg-rose-950/80 text-rose-700 dark:text-rose-300 border-rose-300 dark:border-rose-800 animate-pulse'
        : remainingSec <= 900
          ? 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-300 border-amber-300 dark:border-amber-800'
          : 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-100 border-slate-200 dark:border-slate-700'
      : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 border-slate-200 dark:border-slate-700'

  return (
    <div className="min-h-screen flex flex-col bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-100 select-none sm:select-auto">
      {/* Top Header Bar */}
      <header className="sticky top-0 z-30 border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm">
        <div className="px-3 sm:px-5 h-14 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <button
              type="button"
              onClick={() => navigate('/')}
              title="Return to Dashboard (progress is saved)"
              className="rounded-lg border border-slate-200 dark:border-slate-700 px-2.5 py-1.5 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 shrink-0"
            >
              Dashboard
            </button>
            <h1 className="text-sm sm:text-base font-bold truncate text-slate-900 dark:text-white">
              {test.title}
            </h1>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {/* Timer */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs sm:text-sm font-mono font-bold ${timerColorClass}`}
            >
              <Clock className="h-3.5 w-3.5" />
              <span>
                {remainingSec !== null
                  ? formatCountdown(remainingSec)
                  : formatCountdown(elapsedStopwatchSec)}
              </span>
            </div>

            {enableCalculator && (
              <button
                type="button"
                onClick={() => setCalcOpen((v) => !v)}
                title="Scientific Calculator"
                aria-label="Toggle Scientific Calculator"
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-xs font-semibold transition-colors ${
                  calcOpen
                    ? 'border-indigo-600 bg-indigo-600 text-white shadow-sm'
                    : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700'
                }`}
              >
                <Calculator className="h-4 w-4" />
                <span className="hidden sm:inline">Calc</span>
              </button>
            )}

            <ThemeToggle />

            <button
              type="button"
              onClick={() => setMobilePaletteOpen((v) => !v)}
              className="lg:hidden inline-flex items-center gap-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-2 text-xs font-medium"
            >
              <LayoutGrid className="h-4 w-4" />
            </button>

            <button
              type="button"
              onClick={() => setShowSubmitModal(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 px-3 sm:px-4 py-1.5 sm:py-2 text-xs sm:text-sm font-semibold text-white shadow-sm transition-colors"
            >
              <Send className="h-3.5 w-3.5" />
              <span>Submit Test</span>
            </button>
          </div>
        </div>

        {/* Section Tabs */}
        <div className="px-3 sm:px-5 py-2 border-t border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/70 flex items-center gap-2 overflow-x-auto">
          {availableParts.map((part) => {
            const stats = partSummary[part]
            const isCurrent = currentQuestion.part === part
            const isOver = test.test_type === 'full_mock' && stats.overLimit > 0
            return (
              <button
                key={part}
                type="button"
                onClick={() => {
                  const firstInPart = questions.findIndex((q) => q.part === part)
                  if (firstInPart !== -1) jumpToIndex(firstInPart)
                }}
                className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-semibold border transition-colors shrink-0 ${
                  isCurrent
                    ? 'border-indigo-600 bg-indigo-600 text-white shadow-sm'
                    : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700'
                }`}
              >
                <span>Part {part}</span>
                <span
                  className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                    isOver
                      ? 'bg-amber-400 text-slate-950 font-bold'
                      : isCurrent
                        ? 'bg-indigo-500 text-white'
                        : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300'
                  }`}
                >
                  {stats.answered}/{stats.limit}
                </span>
              </button>
            )
          })}

          {toast && (
            <span className="ml-auto text-xs font-medium text-indigo-600 dark:text-indigo-400 px-2">
              {toast}
            </span>
          )}
        </div>
      </header>

      {/* Soft Warning Banner when exceeding NTA limit */}
      {showOverLimitBanner && (
        <div className="bg-amber-50 dark:bg-amber-950/60 border-b border-amber-200 dark:border-amber-800 px-4 sm:px-6 py-2.5">
          <div className="max-w-7xl mx-auto flex items-start gap-2.5 text-xs sm:text-sm text-amber-900 dark:text-amber-200">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <p>
              <strong>Part {currentQuestion.part} Attempt Limit Exceeded:</strong> You have
              answered <strong>{curPartStats.answered}</strong> questions (limit is{' '}
              <strong>{curPartStats.limit}</strong>). Per NTA rules, only the{' '}
              <strong>first {curPartStats.limit} answered questions in numerical order</strong>{' '}
              will be graded. Click <strong>Clear Response</strong> on an earlier question if you
              want a later one counted instead.
            </p>
          </div>
        </div>
      )}

      {/* Main Split CBT Area */}
      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden">
        {/* Left: Question Pane + Bottom Action Bar */}
        <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-slate-900 lg:border-r border-slate-200 dark:border-slate-800">
          {/* Question Sub-header */}
          <div className="px-4 sm:px-6 py-3 border-b border-slate-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-2 bg-slate-50/70 dark:bg-slate-900">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-bold text-slate-900 dark:text-white">
                Question No. {currentQuestion.question_order}
              </span>
              <span className="rounded-md bg-slate-200/80 dark:bg-slate-800 px-2 py-0.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
                Part {currentQuestion.part} ({PART_MARKING[currentQuestion.part]})
              </span>
              <span className="rounded-md bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-900 px-2 py-0.5 text-xs text-indigo-700 dark:text-indigo-300">
                {currentQuestion.syllabus_unit}
              </span>
              {overLimitResponseIds.has(currentQuestion.response_id) && (
                <span className="rounded-md bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:text-amber-300">
                  Beyond First-{PART_LIMITS[currentQuestion.part]} (Not Graded)
                </span>
              )}
              {currentQuestion.is_checked && currentQuestion.source_label && (
                <span className="rounded-md bg-cyan-100 dark:bg-cyan-950/70 border border-cyan-300 dark:border-cyan-800 px-2 py-0.5 text-xs font-semibold text-cyan-800 dark:text-cyan-300">
                  {currentQuestion.source_label}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleToggleBookmark}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  currentQuestion.is_bookmarked
                    ? 'border-amber-400 bg-amber-50 dark:bg-amber-950/50 text-amber-700 dark:text-amber-300'
                    : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'
                }`}
              >
                <Bookmark
                  className={`h-3.5 w-3.5 ${currentQuestion.is_bookmarked ? 'fill-current' : ''}`}
                />
                <span>{currentQuestion.is_bookmarked ? 'Bookmarked' : 'Bookmark'}</span>
              </button>

              <button
                type="button"
                onClick={() => setFlagTarget({ response_id: currentQuestion.response_id })}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-2.5 py-1.5 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-rose-50 dark:hover:bg-rose-950/40 hover:text-rose-600 dark:hover:text-rose-400 transition-colors"
              >
                <Flag className="h-3.5 w-3.5" />
                <span>Flag</span>
              </button>
            </div>
          </div>

          {/* Question Stem & Options */}
          <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-6">
            <div className="text-sm sm:text-base">
              <MarkdownMath content={currentQuestion.question_text} />
            </div>

            <div className="space-y-3">
              {['A', 'B', 'C', 'D'].map((optKey) => {
                const optText = currentQuestion.options?.[optKey]
                if (optText === undefined) return null
                const isSelected = currentQuestion.selected_option === optKey
                const isChecked = currentQuestion.is_checked
                const isCorrectOpt = currentQuestion.correct_option === optKey

                let cardStyle =
                  'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 hover:border-indigo-400 dark:hover:border-indigo-500'
                if (isChecked) {
                  if (isCorrectOpt) {
                    cardStyle =
                      'border-emerald-500 bg-emerald-50 dark:bg-emerald-950/40 ring-1 ring-emerald-500'
                  } else if (isSelected && !isCorrectOpt) {
                    cardStyle =
                      'border-rose-500 bg-rose-50 dark:bg-rose-950/40 ring-1 ring-rose-500'
                  } else {
                    cardStyle =
                      'border-slate-200 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 opacity-75'
                  }
                } else if (isSelected) {
                  cardStyle =
                    'border-indigo-600 bg-indigo-50 dark:bg-indigo-950/50 ring-2 ring-indigo-600/20'
                }

                return (
                  <button
                    key={optKey}
                    type="button"
                    disabled={isChecked}
                    onClick={() => handleSelectOption(optKey)}
                    className={`w-full text-left rounded-xl border p-3.5 sm:p-4 transition-all flex items-start gap-3.5 ${cardStyle}`}
                  >
                    <span
                      className={`mt-0.5 h-7 w-7 shrink-0 rounded-full flex items-center justify-center text-xs font-bold border ${
                        isChecked && isCorrectOpt
                          ? 'bg-emerald-600 border-emerald-600 text-white'
                          : isChecked && isSelected && !isCorrectOpt
                            ? 'bg-rose-600 border-rose-600 text-white'
                            : isSelected
                              ? 'bg-indigo-600 border-indigo-600 text-white'
                              : 'border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300'
                      }`}
                    >
                      {optKey}
                    </span>
                    <div className="flex-1 text-sm sm:text-base pt-0.5">
                      <MarkdownMath content={optText} inline />
                    </div>
                    {isChecked && isCorrectOpt && (
                      <CheckCircle2 className="h-5 w-5 text-emerald-600 shrink-0 mt-0.5" />
                    )}
                    {isChecked && isSelected && !isCorrectOpt && (
                      <XCircle className="h-5 w-5 text-rose-600 shrink-0 mt-0.5" />
                    )}
                  </button>
                )
              })}
            </div>

            {/* Instant Feedback Check Answer & Explanation */}
            {test.instant_feedback && (
              <div className="pt-2 space-y-4">
                {!currentQuestion.is_checked ? (
                  <button
                    type="button"
                    disabled={!currentQuestion.selected_option || checking}
                    onClick={handleCheckAnswer}
                    className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-45 disabled:cursor-not-allowed px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors"
                  >
                    <Sparkles className="h-4 w-4" />
                    <span>{checking ? 'Checking...' : 'Check Answer'}</span>
                  </button>
                ) : (
                  <div
                    className={`rounded-2xl border p-4 sm:p-5 space-y-2.5 ${
                      currentQuestion.is_correct
                        ? 'border-emerald-200 dark:border-emerald-800 bg-emerald-50/70 dark:bg-emerald-950/30'
                        : 'border-rose-200 dark:border-rose-800 bg-rose-50/70 dark:bg-rose-950/30'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 text-sm font-bold">
                        {currentQuestion.is_correct ? (
                          <>
                            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                            <span className="text-emerald-800 dark:text-emerald-300">
                              Correct Answer ({currentQuestion.correct_option})
                            </span>
                          </>
                        ) : (
                          <>
                            <XCircle className="h-4 w-4 text-rose-600" />
                            <span className="text-rose-800 dark:text-rose-300">
                              Incorrect — Correct Option is {currentQuestion.correct_option}
                            </span>
                          </>
                        )}
                      </div>
                      {currentQuestion.source_label && (
                        <span className="rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 px-2 py-0.5 text-xs font-semibold text-slate-700 dark:text-slate-200">
                          {currentQuestion.source_label}
                        </span>
                      )}
                    </div>
                    {currentQuestion.explanation ? (
                      <div className="text-sm pt-1 border-t border-slate-200/70 dark:border-slate-700/60">
                        <MarkdownMath content={currentQuestion.explanation} />
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 dark:text-slate-400 italic">
                        Official previous year question (answer key verified; no detailed write-up stored).
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Bottom Action Bar */}
          <div className="border-t border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 px-3 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={currentIdx === 0}
                onClick={() => jumpToIndex(currentIdx - 1)}
                className="inline-flex items-center gap-1 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-40 px-3 py-2 text-xs sm:text-sm font-medium transition-colors"
              >
                <ChevronLeft className="h-4 w-4" />
                <span>Previous</span>
              </button>

              <button
                type="button"
                disabled={!currentQuestion.selected_option || currentQuestion.is_checked}
                onClick={handleClearResponse}
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-40 px-3 py-2 text-xs sm:text-sm font-medium text-slate-700 dark:text-slate-200 transition-colors"
              >
                <Eraser className="h-3.5 w-3.5" />
                <span>Clear Response</span>
              </button>
            </div>

            <div className="flex items-center gap-2 ml-auto">
              <button
                type="button"
                onClick={handleMarkAndNext}
                className="rounded-xl bg-purple-600 hover:bg-purple-700 px-3 sm:px-4 py-2 text-xs sm:text-sm font-semibold text-white transition-colors"
              >
                Mark for Review & Next
              </button>

              <button
                type="button"
                onClick={handleSaveAndNext}
                className="inline-flex items-center gap-1 rounded-xl bg-emerald-600 hover:bg-emerald-700 px-4 sm:px-5 py-2 text-xs sm:text-sm font-semibold text-white shadow-sm transition-colors"
              >
                <span>Save & Next</span>
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {/* Right: 5-State Question Palette (desktop fixed sidebar + mobile drawer) */}
        <aside
          className={`${
            mobilePaletteOpen ? 'fixed inset-0 z-40 flex justify-end bg-slate-950/50 lg:static lg:bg-transparent' : 'hidden lg:flex'
          } lg:w-80 xl:w-96 flex-col bg-slate-50 dark:bg-slate-950 shrink-0`}
        >
          <div className="w-80 sm:w-96 lg:w-full h-full flex flex-col bg-slate-50 dark:bg-slate-900 border-l border-slate-200 dark:border-slate-800">
            <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
              <h2 className="text-sm font-bold text-slate-900 dark:text-white">
                Question Palette
              </h2>
              <button
                type="button"
                onClick={() => setMobilePaletteOpen(false)}
                className="lg:hidden rounded-lg p-1 text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-800"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* 5-State Legend */}
            <div className="p-4 border-b border-slate-200 dark:border-slate-800 grid grid-cols-2 gap-2.5 text-xs">
              <LegendItem
                badgeClass="bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200"
                count={stateCounts.not_visited}
                label="Not Visited"
              />
              <LegendItem
                badgeClass="bg-rose-500 text-white"
                count={stateCounts.not_answered}
                label="Not Answered"
              />
              <LegendItem
                badgeClass="bg-emerald-600 text-white"
                count={stateCounts.answered}
                label="Answered"
              />
              <LegendItem
                badgeClass="bg-purple-600 text-white"
                count={stateCounts.marked}
                label="Marked"
              />
              <div className="col-span-2">
                <LegendItem
                  badgeClass="bg-purple-600 text-white relative"
                  dot
                  count={stateCounts.answered_marked}
                  label="Answered & Marked for Review"
                />
              </div>
            </div>

            {/* Number Grid */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {availableParts.map((part) => {
                const partQs = questions
                  .map((q, idx) => ({ q, idx }))
                  .filter(({ q }) => q.part === part)
                return (
                  <div key={part}>
                    <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
                      <span>Part {part}</span>
                      <span className="font-mono">
                        {partSummary[part].answered}/{partSummary[part].limit}
                      </span>
                    </div>
                    <div className="grid grid-cols-5 gap-2">
                      {partQs.map(({ q, idx }) => {
                        const isActive = idx === currentIdx
                        let btnColor =
                          'bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200'
                        if (q.status === 'not_answered') {
                          btnColor = 'bg-rose-500 text-white'
                        } else if (q.status === 'answered') {
                          btnColor = 'bg-emerald-600 text-white'
                        } else if (q.status === 'marked') {
                          btnColor = 'bg-purple-600 text-white'
                        } else if (q.status === 'answered_marked') {
                          btnColor = 'bg-purple-600 text-white'
                        }
                        const isOver = overLimitResponseIds.has(q.response_id)

                        return (
                          <button
                            key={q.response_id}
                            type="button"
                            onClick={() => jumpToIndex(idx)}
                            className={`relative h-10 rounded-lg font-mono text-xs font-bold transition-transform ${btnColor} ${
                              isActive
                                ? 'ring-2 ring-offset-2 ring-indigo-600 dark:ring-offset-slate-900 scale-105'
                                : 'hover:opacity-90'
                            } ${isOver ? 'outline outline-2 outline-amber-400' : ''}`}
                          >
                            {q.question_order}
                            {q.status === 'answered_marked' && (
                              <span className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-emerald-400 ring-2 ring-white dark:ring-slate-900" />
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </aside>
      </div>

      {/* Submit Confirmation Modal */}
      {showSubmitModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 shadow-2xl overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
              <h3 className="text-lg font-bold text-slate-900 dark:text-white">
                Confirm Test Submission
              </h3>
              <button
                type="button"
                onClick={() => setShowSubmitModal(false)}
                className="rounded-lg p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <p className="text-sm text-slate-600 dark:text-slate-300">
                Review your part-wise attempt summary before final submission.
              </p>

              <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 dark:bg-slate-950 text-slate-500 dark:text-slate-400 uppercase">
                    <tr>
                      <th className="px-4 py-2.5">Part</th>
                      <th className="px-3 py-2.5">Answered</th>
                      <th className="px-3 py-2.5">Unanswered</th>
                      <th className="px-3 py-2.5">Over-Limit</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {availableParts.map((part) => {
                      const s = partSummary[part]
                      return (
                        <tr key={part}>
                          <td className="px-4 py-2.5 font-semibold">Part {part}</td>
                          <td className="px-3 py-2.5 font-mono text-emerald-600 dark:text-emerald-400 font-semibold">
                            {s.answered} / {s.limit}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-slate-500">
                            {s.unanswered}
                          </td>
                          <td className="px-3 py-2.5 font-mono">
                            {s.overLimit > 0 ? (
                              <span className="rounded bg-amber-100 dark:bg-amber-950 px-1.5 py-0.5 text-amber-800 dark:text-amber-300 font-semibold">
                                {s.overLimit} ignored
                              </span>
                            ) : (
                              '0'
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowSubmitModal(false)}
                  className="rounded-xl px-4 py-2 text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  Resume Exam
                </button>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={handleFinalSubmit}
                  className="rounded-xl bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 px-5 py-2 text-sm font-semibold text-white transition-colors"
                >
                  {submitting ? 'Submitting...' : 'Confirm & Submit'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <ScientificCalculator
        isOpen={calcOpen && enableCalculator}
        onClose={() => setCalcOpen(false)}
        state={calcState}
        setState={setCalcState}
      />

      <FlagModal
        isOpen={Boolean(flagTarget)}
        onClose={() => setFlagTarget(null)}
        target={flagTarget}
        onFlagged={() => showToast('Question flagged for review')}
      />
    </div>
  )
}

function LegendItem({ badgeClass, count, label, dot = false }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`h-6 w-7 rounded-md font-mono text-[11px] font-bold flex items-center justify-center shrink-0 ${badgeClass}`}
      >
        {count}
        {dot && (
          <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-400 ring-1 ring-white dark:ring-slate-900" />
        )}
      </span>
      <span className="text-slate-600 dark:text-slate-300 truncate">{label}</span>
    </div>
  )
}
