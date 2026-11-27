import { useCallback, useEffect, useRef, useState } from 'react'
import { GripHorizontal, X } from 'lucide-react'
import {
  applyUnary,
  clearAll,
  clearEntry,
  evaluateEquals,
  inputBackspace,
  inputBinaryOp,
  inputCloseParen,
  inputConstant,
  inputDecimal,
  inputDigit,
  inputExp,
  inputOpenParen,
  memoryAction,
  toggleAngleMode,
  toggleSign,
} from '../utils/calculatorEngine'

const PANEL_PADDING = 8

export default function ScientificCalculator({ isOpen, onClose, state, setState }) {
  const panelRef = useRef(null)
  const [pos, setPos] = useState(() => {
    if (typeof window === 'undefined') return { x: 24, y: 72 }
    return {
      x: Math.max(PANEL_PADDING, window.innerWidth - 460),
      y: 70,
    }
  })
  const dragRef = useRef(null)

  const clampPosition = useCallback((x, y) => {
    if (typeof window === 'undefined') return { x, y }
    const rect = panelRef.current?.getBoundingClientRect()
    const width = rect?.width || 440
    const height = rect?.height || 460
    const maxX = Math.max(PANEL_PADDING, window.innerWidth - width - PANEL_PADDING)
    const maxY = Math.max(PANEL_PADDING, window.innerHeight - height - PANEL_PADDING)
    return {
      x: Math.min(Math.max(PANEL_PADDING, x), maxX),
      y: Math.min(Math.max(PANEL_PADDING, y), maxY),
    }
  }, [])

  // Re-clamp on open or viewport resize so it never clips off-screen
  useEffect(() => {
    if (!isOpen) return
    setPos((prev) => clampPosition(prev.x, prev.y))
    const handleResize = () => {
      setPos((prev) => clampPosition(prev.x, prev.y))
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [isOpen, clampPosition])

  const handleHeaderPointerDown = (e) => {
    if (e.target.closest('button')) return
    e.preventDefault()
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origX: pos.x,
      origY: pos.y,
    }

    const onPointerMove = (moveEvt) => {
      if (!dragRef.current) return
      const dx = moveEvt.clientX - dragRef.current.startX
      const dy = moveEvt.clientY - dragRef.current.startY
      setPos(clampPosition(dragRef.current.origX + dx, dragRef.current.origY + dy))
    }

    const onPointerUp = () => {
      dragRef.current = null
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerUp)
  }

  if (!isOpen) return null

  const run = (fn, ...args) => {
    setState((prev) => fn(prev, ...args))
  }

  // Distinct key group styling (Light + Dark mode)
  const keyCls = {
    num: 'bg-white dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-900 dark:text-white border-slate-300 dark:border-slate-600 font-semibold',
    op: 'bg-indigo-50 dark:bg-indigo-950/70 hover:bg-indigo-100 dark:hover:bg-indigo-900/70 text-indigo-800 dark:text-indigo-200 border-indigo-200 dark:border-indigo-800 font-semibold',
    sci: 'bg-slate-100 dark:bg-slate-800/80 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 border-slate-200 dark:border-slate-700 text-[11px] font-medium',
    mem: 'bg-sky-50 dark:bg-sky-950/60 hover:bg-sky-100 dark:hover:bg-sky-900/60 text-sky-800 dark:text-sky-200 border-sky-200 dark:border-sky-800 text-[11px] font-semibold',
    clear:
      'bg-rose-50 dark:bg-rose-950/60 hover:bg-rose-100 dark:hover:bg-rose-900/60 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-800 font-semibold',
    eq: 'bg-emerald-600 hover:bg-emerald-700 text-white border-emerald-700 font-bold shadow-sm',
  }

  const buttons = [
    // Row 1
    { label: '(', group: 'op', onClick: () => run(inputOpenParen) },
    { label: ')', group: 'op', onClick: () => run(inputCloseParen) },
    { label: 'mod', group: 'op', onClick: () => run(inputBinaryOp, 'mod') },
    { label: 'MC', group: 'mem', onClick: () => run(memoryAction, 'MC') },
    { label: 'MR', group: 'mem', onClick: () => run(memoryAction, 'MR') },
    { label: 'MS', group: 'mem', onClick: () => run(memoryAction, 'MS') },
    { label: 'M+', group: 'mem', onClick: () => run(memoryAction, 'M+') },
    { label: 'M-', group: 'mem', onClick: () => run(memoryAction, 'M-') },

    // Row 2
    { label: 'sinh', group: 'sci', onClick: () => run(applyUnary, 'sinh') },
    { label: 'cosh', group: 'sci', onClick: () => run(applyUnary, 'cosh') },
    { label: 'tanh', group: 'sci', onClick: () => run(applyUnary, 'tanh') },
    { label: '←', group: 'clear', title: 'Backspace', onClick: () => run(inputBackspace) },
    { label: 'CE', group: 'clear', title: 'Clear Entry', onClick: () => run(clearEntry) },
    { label: 'C', group: 'clear', title: 'All Clear', onClick: () => run(clearAll) },
    { label: '+/-', group: 'op', onClick: () => run(toggleSign) },
    { label: '√x', group: 'sci', onClick: () => run(applyUnary, 'sqrt') },

    // Row 3
    { label: 'sinh⁻¹', group: 'sci', onClick: () => run(applyUnary, 'asinh') },
    { label: 'cosh⁻¹', group: 'sci', onClick: () => run(applyUnary, 'acosh') },
    { label: 'tanh⁻¹', group: 'sci', onClick: () => run(applyUnary, 'atanh') },
    { label: '7', group: 'num', onClick: () => run(inputDigit, '7') },
    { label: '8', group: 'num', onClick: () => run(inputDigit, '8') },
    { label: '9', group: 'num', onClick: () => run(inputDigit, '9') },
    { label: '÷', group: 'op', onClick: () => run(inputBinaryOp, '/') },
    { label: '1/x', group: 'sci', onClick: () => run(applyUnary, 'inv') },

    // Row 4
    { label: 'sin', group: 'sci', onClick: () => run(applyUnary, 'sin') },
    { label: 'cos', group: 'sci', onClick: () => run(applyUnary, 'cos') },
    { label: 'tan', group: 'sci', onClick: () => run(applyUnary, 'tan') },
    { label: '4', group: 'num', onClick: () => run(inputDigit, '4') },
    { label: '5', group: 'num', onClick: () => run(inputDigit, '5') },
    { label: '6', group: 'num', onClick: () => run(inputDigit, '6') },
    { label: '×', group: 'op', onClick: () => run(inputBinaryOp, '*') },
    { label: 'x²', group: 'sci', onClick: () => run(applyUnary, 'sqr') },

    // Row 5
    { label: 'sin⁻¹', group: 'sci', onClick: () => run(applyUnary, 'asin') },
    { label: 'cos⁻¹', group: 'sci', onClick: () => run(applyUnary, 'acos') },
    { label: 'tan⁻¹', group: 'sci', onClick: () => run(applyUnary, 'atan') },
    { label: '1', group: 'num', onClick: () => run(inputDigit, '1') },
    { label: '2', group: 'num', onClick: () => run(inputDigit, '2') },
    { label: '3', group: 'num', onClick: () => run(inputDigit, '3') },
    { label: '−', group: 'op', onClick: () => run(inputBinaryOp, '-') },
    { label: 'x³', group: 'sci', onClick: () => run(applyUnary, 'cube') },

    // Row 6
    { label: 'ln', group: 'sci', onClick: () => run(applyUnary, 'ln') },
    { label: 'log', group: 'sci', onClick: () => run(applyUnary, 'log') },
    { label: 'log₂x', group: 'sci', onClick: () => run(applyUnary, 'log2') },
    { label: '0', group: 'num', onClick: () => run(inputDigit, '0') },
    { label: '.', group: 'num', onClick: () => run(inputDecimal) },
    { label: 'Exp', group: 'sci', onClick: () => run(inputExp) },
    { label: '+', group: 'op', onClick: () => run(inputBinaryOp, '+') },
    { label: 'n!', group: 'sci', onClick: () => run(applyUnary, 'fact') },

    // Row 7
    { label: 'e^x', group: 'sci', onClick: () => run(applyUnary, 'exp') },
    { label: '10^x', group: 'sci', onClick: () => run(applyUnary, 'pow10') },
    { label: 'x^y', group: 'op', onClick: () => run(inputBinaryOp, '^') },
    { label: 'y√x', group: 'op', onClick: () => run(inputBinaryOp, 'yroot') },
    { label: '∛x', group: 'sci', onClick: () => run(applyUnary, 'cbrt') },
    { label: 'π', group: 'sci', onClick: () => run(inputConstant, 'pi') },
    { label: 'e', group: 'sci', onClick: () => run(inputConstant, 'e') },
    { label: '=', group: 'eq', onClick: () => run(evaluateEquals) },
  ]

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Scientific Calculator"
      style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
      className="fixed z-40 w-[436px] max-w-[calc(100vw-16px)] rounded-2xl border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 shadow-2xl select-none overflow-hidden"
    >
      {/* Draggable Header Bar */}
      <div
        onPointerDown={handleHeaderPointerDown}
        className="flex items-center justify-between px-3.5 py-2.5 bg-indigo-700 dark:bg-slate-800 text-white cursor-grab active:cursor-grabbing border-b border-indigo-800 dark:border-slate-700 touch-none"
      >
        <div className="flex items-center gap-2 text-xs font-bold tracking-wide">
          <GripHorizontal className="h-4 w-4 opacity-80" />
          <span>Scientific Calculator</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close calculator"
          className="rounded-md p-1 hover:bg-white/15 text-white/90 hover:text-white transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Dual Display + Angle Mode + M Indicator */}
      <div className="p-3 space-y-2 bg-white dark:bg-slate-950/60 border-b border-slate-200 dark:border-slate-800">
        <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 px-3 py-2 text-right">
          <div
            data-testid="calc-expression"
            className="h-4 text-[11px] font-mono text-slate-500 dark:text-slate-400 truncate"
          >
            {state.expression || '\u00A0'}
          </div>
          <div className="flex items-center justify-between gap-2 mt-0.5">
            <span
              className={`inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[10px] font-bold font-mono ${
                state.memory !== 0
                  ? 'bg-sky-100 dark:bg-sky-900 text-sky-700 dark:text-sky-200'
                  : 'opacity-0 pointer-events-none'
              }`}
            >
              M
            </span>
            <div
              data-testid="calc-display"
              className={`text-xl font-mono font-bold tracking-tight truncate ${
                state.hasError
                  ? 'text-rose-600 dark:text-rose-400'
                  : 'text-slate-900 dark:text-white'
              }`}
            >
              {state.display}
            </div>
          </div>
        </div>

        {/* Deg / Rad Radio Toggle */}
        <div className="flex items-center justify-between px-1 text-xs">
          <div className="inline-flex items-center gap-3">
            {['Deg', 'Rad'].map((mode) => (
              <label
                key={mode}
                className="inline-flex items-center gap-1.5 cursor-pointer text-slate-700 dark:text-slate-300 font-medium"
              >
                <input
                  type="radio"
                  name="calc-angle-mode"
                  value={mode}
                  checked={state.angleMode === mode}
                  onChange={() => run(toggleAngleMode, mode)}
                  className="accent-indigo-600 cursor-pointer"
                />
                <span>{mode}</span>
              </label>
            ))}
          </div>
          <span className="text-[10px] text-slate-400 dark:text-slate-500">
            Mouse-driven · TCS iON mode
          </span>
        </div>
      </div>

      {/* 8-Column Keypad */}
      <div className="p-2.5 grid grid-cols-8 gap-1.5 bg-slate-100/80 dark:bg-slate-900">
        {buttons.map((btn) => (
          <button
            key={btn.label}
            type="button"
            title={btn.title || btn.label}
            onClick={btn.onClick}
            className={`h-9 rounded-lg border text-xs transition-colors active:scale-95 flex items-center justify-center ${
              keyCls[btn.group]
            }`}
          >
            {btn.label}
          </button>
        ))}
      </div>
    </div>
  )
}
