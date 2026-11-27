const OP_INFO = {
  '+': { prec: 1, assoc: 'left', symbol: '+' },
  '-': { prec: 1, assoc: 'left', symbol: '−' },
  '*': { prec: 2, assoc: 'left', symbol: '×' },
  '/': { prec: 2, assoc: 'left', symbol: '÷' },
  mod: { prec: 2, assoc: 'left', symbol: 'mod' },
  '^': { prec: 3, assoc: 'right', symbol: '^' },
  yroot: { prec: 3, assoc: 'right', symbol: 'yroot' },
}

export function createInitialCalculatorState() {
  return {
    display: '0',
    expression: '',
    angleMode: 'Deg',
    memory: 0,
    tokens: [],
    isNewEntry: true,
    justEvaluated: false,
    hasError: false,
    lastUnaryExpr: null,
  }
}

export function formatNumber(val) {
  if (typeof val !== 'number' || !Number.isFinite(val)) {
    return null
  }
  if (Object.is(val, -0) || val === 0) return '0'
  const rounded = Number(val.toPrecision(12))
  if (!Number.isFinite(rounded)) return null
  if (Object.is(rounded, -0) || rounded === 0) return '0'
  return rounded.toString()
}

function snapTrigZero(val) {
  return Math.abs(val) < 1e-14 ? 0 : val
}

function parseDisplayValue(display) {
  if (!display || display === 'Error' || display === 'Invalid input') {
    return NaN
  }
  // Handle incomplete exponent entry like "6.022e+" or "6.022e-"
  const normalized = display.replace(/e[+-]?$/i, '')
  return Number(normalized)
}

function tokensToExpression(tokens) {
  return tokens
    .map((t) => {
      if (t.type === 'num') return t.label
      if (t.type === 'op') return ` ${OP_INFO[t.op].symbol} `
      if (t.type === 'lparen') return '('
      if (t.type === 'rparen') return ')'
      return ''
    })
    .join('')
}

/**
 * Shunting-yard infix -> RPN -> evaluation (no eval()).
 */
export function evaluateTokens(tokens) {
  const output = []
  const opStack = []

  for (const token of tokens) {
    if (token.type === 'num') {
      output.push(token)
    } else if (token.type === 'op') {
      const o1 = OP_INFO[token.op]
      while (opStack.length > 0) {
        const top = opStack[opStack.length - 1]
        if (top.type !== 'op') break
        const o2 = OP_INFO[top.op]
        if (
          (o1.assoc === 'left' && o1.prec <= o2.prec) ||
          (o1.assoc === 'right' && o1.prec < o2.prec)
        ) {
          output.push(opStack.pop())
        } else {
          break
        }
      }
      opStack.push(token)
    } else if (token.type === 'lparen') {
      opStack.push(token)
    } else if (token.type === 'rparen') {
      let foundLeft = false
      while (opStack.length > 0) {
        const top = opStack.pop()
        if (top.type === 'lparen') {
          foundLeft = true
          break
        }
        output.push(top)
      }
      if (!foundLeft) {
        return { error: 'Invalid input' }
      }
    }
  }

  while (opStack.length > 0) {
    const top = opStack.pop()
    if (top.type === 'lparen' || top.type === 'rparen') {
      return { error: 'Invalid input' }
    }
    output.push(top)
  }

  const stack = []
  for (const token of output) {
    if (token.type === 'num') {
      stack.push(token.value)
      continue
    }
    if (stack.length < 2) {
      return { error: 'Invalid input' }
    }
    const b = stack.pop()
    const a = stack.pop()
    let res

    switch (token.op) {
      case '+':
        res = a + b
        break
      case '-':
        res = a - b
        break
      case '*':
        res = a * b
        break
      case '/':
        if (b === 0) return { error: 'Error' }
        res = a / b
        break
      case 'mod':
        if (b === 0) return { error: 'Error' }
        res = a % b
        break
      case '^':
        res = Math.pow(a, b)
        break
      case 'yroot':
        if (b === 0) return { error: 'Error' }
        if (a < 0) {
          if (Number.isInteger(b) && Math.abs(b % 2) === 1) {
            res = -Math.pow(-a, 1 / b)
          } else {
            return { error: 'Invalid input' }
          }
        } else {
          res = Math.pow(a, 1 / b)
        }
        break
      default:
        return { error: 'Invalid input' }
    }

    if (!Number.isFinite(res)) {
      return { error: 'Error' }
    }
    stack.push(res)
  }

  if (stack.length !== 1) {
    return { error: 'Invalid input' }
  }
  return { value: stack[0] }
}

function errorState(prevState, message = 'Error') {
  return {
    ...prevState,
    display: message,
    expression: '',
    tokens: [],
    isNewEntry: true,
    justEvaluated: false,
    hasError: true,
    lastUnaryExpr: null,
  }
}

function countOpenParens(tokens) {
  let open = 0
  for (const t of tokens) {
    if (t.type === 'lparen') open += 1
    else if (t.type === 'rparen') open -= 1
  }
  return open
}

export function inputDigit(state, digit) {
  if (state.hasError || state.justEvaluated) {
    return {
      ...state,
      display: digit,
      expression: state.justEvaluated ? '' : state.expression,
      tokens: state.justEvaluated ? [] : state.tokens,
      isNewEntry: false,
      justEvaluated: false,
      hasError: false,
      lastUnaryExpr: null,
    }
  }

  if (state.isNewEntry) {
    return {
      ...state,
      display: digit,
      isNewEntry: false,
      lastUnaryExpr: null,
    }
  }

  if (state.display === '0') {
    return { ...state, display: digit }
  }
  if (state.display === '-0') {
    return { ...state, display: `-${digit}` }
  }
  if (state.display.length >= 22) {
    return state
  }
  return { ...state, display: state.display + digit }
}

export function inputDecimal(state) {
  if (state.hasError || state.justEvaluated) {
    return {
      ...state,
      display: '0.',
      expression: '',
      tokens: [],
      isNewEntry: false,
      justEvaluated: false,
      hasError: false,
      lastUnaryExpr: null,
    }
  }
  if (state.isNewEntry) {
    return {
      ...state,
      display: '0.',
      isNewEntry: false,
      lastUnaryExpr: null,
    }
  }
  if (state.display.includes('.') || /e/i.test(state.display)) {
    return state
  }
  return { ...state, display: `${state.display}.` }
}

export function inputExp(state) {
  if (state.hasError) return state
  if (state.justEvaluated) {
    return {
      ...state,
      display: `${state.display}e+`,
      expression: '',
      tokens: [],
      isNewEntry: false,
      justEvaluated: false,
      lastUnaryExpr: null,
    }
  }
  if (/e/i.test(state.display)) {
    return state
  }
  const base = state.isNewEntry ? '1' : state.display
  return {
    ...state,
    display: `${base}e+`,
    isNewEntry: false,
    lastUnaryExpr: null,
  }
}

export function inputBackspace(state) {
  if (state.hasError) {
    return createInitialCalculatorStateWithPrefs(state)
  }
  if (state.isNewEntry) {
    return state
  }
  if (state.display.endsWith('e+') || state.display.endsWith('e-')) {
    return { ...state, display: state.display.slice(0, -2) || '0' }
  }
  if (
    state.display.length <= 1 ||
    (state.display.length === 2 && state.display.startsWith('-'))
  ) {
    return { ...state, display: '0', isNewEntry: true }
  }
  return { ...state, display: state.display.slice(0, -1) }
}

function createInitialCalculatorStateWithPrefs(state) {
  return {
    ...createInitialCalculatorState(),
    angleMode: state.angleMode,
    memory: state.memory,
  }
}

export function clearEntry(state) {
  return {
    ...state,
    display: '0',
    isNewEntry: true,
    hasError: false,
    lastUnaryExpr: null,
    expression: state.justEvaluated ? '' : state.expression,
    tokens: state.justEvaluated ? [] : state.tokens,
    justEvaluated: false,
  }
}

export function clearAll(state) {
  return createInitialCalculatorStateWithPrefs(state)
}

export function toggleAngleMode(state, mode) {
  if (mode !== 'Deg' && mode !== 'Rad') return state
  return { ...state, angleMode: mode }
}

export function toggleSign(state) {
  if (state.hasError) return state
  // If editing scientific exponent like 6.022e+23 or 6.022e-19, flip exponent sign
  const expMatch = /^(.*e)([+-])(.*)$/i.exec(state.display)
  if (expMatch && !state.isNewEntry) {
    const [, prefix, sign, rest] = expMatch
    const flipped = sign === '+' ? '-' : '+'
    return { ...state, display: `${prefix}${flipped}${rest}` }
  }

  if (state.display === '0') return state
  const nextDisplay = state.display.startsWith('-')
    ? state.display.slice(1)
    : `-${state.display}`

  return {
    ...state,
    display: nextDisplay,
    lastUnaryExpr: state.lastUnaryExpr ? `-(${state.lastUnaryExpr})` : null,
  }
}

export function inputConstant(state, constantName) {
  const val = constantName === 'pi' ? Math.PI : Math.E
  const label = constantName === 'pi' ? 'π' : 'e'
  const formatted = formatNumber(val)

  const baseTokens = state.justEvaluated || state.hasError ? [] : state.tokens
  const prefixExpr = tokensToExpression(baseTokens)

  return {
    ...state,
    display: formatted,
    tokens: baseTokens,
    expression: prefixExpr + label,
    isNewEntry: true,
    justEvaluated: false,
    hasError: false,
    lastUnaryExpr: label,
  }
}

function factorial(n) {
  let acc = 1
  for (let i = 2; i <= n; i += 1) {
    acc *= i
  }
  return acc
}

export function applyUnary(state, fn) {
  if (state.hasError) return state
  const x = parseDisplayValue(state.display)
  if (!Number.isFinite(x)) {
    return errorState(state, 'Invalid input')
  }

  const toRad = (v) => (state.angleMode === 'Deg' ? (v * Math.PI) / 180 : v)
  const fromRad = (v) => (state.angleMode === 'Deg' ? (v * 180) / Math.PI : v)

  let result
  let labelPrefix = fn

  switch (fn) {
    case 'sin':
      result = snapTrigZero(Math.sin(toRad(x)))
      break
    case 'cos':
      result = snapTrigZero(Math.cos(toRad(x)))
      break
    case 'tan': {
      const c = snapTrigZero(Math.cos(toRad(x)))
      if (c === 0) return errorState(state, 'Error')
      result = snapTrigZero(Math.tan(toRad(x)))
      break
    }
    case 'asin':
      if (x < -1 || x > 1) return errorState(state, 'Invalid input')
      result = fromRad(Math.asin(x))
      labelPrefix = 'sin⁻¹'
      break
    case 'acos':
      if (x < -1 || x > 1) return errorState(state, 'Invalid input')
      result = fromRad(Math.acos(x))
      labelPrefix = 'cos⁻¹'
      break
    case 'atan':
      result = fromRad(Math.atan(x))
      labelPrefix = 'tan⁻¹'
      break
    case 'sinh':
      result = Math.sinh(x)
      break
    case 'cosh':
      result = Math.cosh(x)
      break
    case 'tanh':
      result = Math.tanh(x)
      break
    case 'asinh':
      result = Math.asinh(x)
      labelPrefix = 'sinh⁻¹'
      break
    case 'acosh':
      if (x < 1) return errorState(state, 'Invalid input')
      result = Math.acosh(x)
      labelPrefix = 'cosh⁻¹'
      break
    case 'atanh':
      if (x <= -1 || x >= 1) return errorState(state, 'Invalid input')
      result = Math.atanh(x)
      labelPrefix = 'tanh⁻¹'
      break
    case 'log':
      if (x <= 0) return errorState(state, 'Invalid input')
      result = Math.log10(x)
      break
    case 'ln':
      if (x <= 0) return errorState(state, 'Invalid input')
      result = Math.log(x)
      break
    case 'log2':
      if (x <= 0) return errorState(state, 'Invalid input')
      result = Math.log2(x)
      labelPrefix = 'log₂'
      break
    case 'pow10':
      result = Math.pow(10, x)
      labelPrefix = '10^'
      break
    case 'exp':
      result = Math.exp(x)
      labelPrefix = 'e^'
      break
    case 'sqr':
      result = x * x
      break
    case 'cube':
      result = x * x * x
      break
    case 'sqrt':
      if (x < 0) return errorState(state, 'Invalid input')
      result = Math.sqrt(x)
      labelPrefix = '√'
      break
    case 'cbrt':
      result = Math.cbrt(x)
      labelPrefix = '∛'
      break
    case 'inv':
      if (x === 0) return errorState(state, 'Error')
      result = 1 / x
      labelPrefix = '1/'
      break
    case 'fact':
      if (!Number.isInteger(x) || x < 0 || x > 170) {
        return errorState(state, 'Invalid input')
      }
      result = factorial(x)
      break
    default:
      return state
  }

  const formatted = formatNumber(result)
  if (formatted === null) {
    return errorState(state, 'Error')
  }

  const innerLabel = state.lastUnaryExpr || formatNumber(x) || state.display
  let exprLabel
  if (fn === 'sqr') exprLabel = `(${innerLabel})²`
  else if (fn === 'cube') exprLabel = `(${innerLabel})³`
  else if (fn === 'fact') exprLabel = `fact(${innerLabel})`
  else if (fn === 'inv') exprLabel = `1/(${innerLabel})`
  else if (fn === 'pow10') exprLabel = `10^(${innerLabel})`
  else if (fn === 'exp') exprLabel = `e^(${innerLabel})`
  else exprLabel = `${labelPrefix}(${innerLabel})`

  const baseTokens = state.justEvaluated ? [] : state.tokens
  const prefixExpr = tokensToExpression(baseTokens)

  return {
    ...state,
    display: formatted,
    tokens: baseTokens,
    expression: prefixExpr + exprLabel,
    isNewEntry: true,
    justEvaluated: false,
    hasError: false,
    lastUnaryExpr: exprLabel,
  }
}

export function inputBinaryOp(state, op) {
  if (state.hasError || !OP_INFO[op]) return state

  const val = parseDisplayValue(state.display)
  if (!Number.isFinite(val)) {
    return errorState(state, 'Invalid input')
  }

  let nextTokens = state.justEvaluated ? [] : [...state.tokens]
  const last = nextTokens[nextTokens.length - 1]

  if (last && last.type === 'op' && state.isNewEntry && !state.lastUnaryExpr) {
    // Replace trailing operator when user switches operator before typing next operand
    nextTokens[nextTokens.length - 1] = { type: 'op', op }
  } else if (last && last.type === 'rparen') {
    nextTokens.push({ type: 'op', op })
  } else {
    const label = state.lastUnaryExpr || formatNumber(val) || state.display
    nextTokens.push({ type: 'num', value: val, label })
    nextTokens.push({ type: 'op', op })
  }

  return {
    ...state,
    tokens: nextTokens,
    expression: tokensToExpression(nextTokens),
    isNewEntry: true,
    justEvaluated: false,
    lastUnaryExpr: null,
  }
}

export function inputOpenParen(state) {
  if (state.hasError) return state
  const nextTokens = state.justEvaluated ? [] : [...state.tokens]
  const last = nextTokens[nextTokens.length - 1]

  if (last && (last.type === 'num' || last.type === 'rparen')) {
    nextTokens.push({ type: 'op', op: '*' })
  } else if (!state.isNewEntry && nextTokens.length === 0) {
    // User typed a number then '(' without explicit '*'
    const val = parseDisplayValue(state.display)
    if (Number.isFinite(val)) {
      nextTokens.push({ type: 'num', value: val, label: formatNumber(val) || state.display })
      nextTokens.push({ type: 'op', op: '*' })
    }
  }

  nextTokens.push({ type: 'lparen' })
  return {
    ...state,
    tokens: nextTokens,
    expression: tokensToExpression(nextTokens),
    isNewEntry: true,
    justEvaluated: false,
    lastUnaryExpr: null,
  }
}

export function inputCloseParen(state) {
  if (state.hasError) return state
  if (countOpenParens(state.tokens) <= 0) {
    return state
  }

  const nextTokens = [...state.tokens]
  const last = nextTokens[nextTokens.length - 1]
  if (!last || last.type === 'lparen' || last.type === 'op') {
    const val = parseDisplayValue(state.display)
    if (!Number.isFinite(val)) return errorState(state, 'Invalid input')
    const label = state.lastUnaryExpr || formatNumber(val) || state.display
    nextTokens.push({ type: 'num', value: val, label })
  }
  nextTokens.push({ type: 'rparen' })

  // Preview value inside the just-closed parenthesis slice
  let depth = 1
  let startIdx = nextTokens.length - 2
  while (startIdx >= 0 && depth > 0) {
    if (nextTokens[startIdx].type === 'rparen') depth += 1
    else if (nextTokens[startIdx].type === 'lparen') depth -= 1
    if (depth > 0) startIdx -= 1
  }
  let nextDisplay = state.display
  if (startIdx >= 0) {
    const subEval = evaluateTokens(nextTokens.slice(startIdx + 1, -1))
    if (subEval.error) return errorState(state, subEval.error)
    const fmt = formatNumber(subEval.value)
    if (fmt === null) return errorState(state, 'Error')
    nextDisplay = fmt
  }

  return {
    ...state,
    display: nextDisplay,
    tokens: nextTokens,
    expression: tokensToExpression(nextTokens),
    isNewEntry: true,
    justEvaluated: false,
    lastUnaryExpr: null,
  }
}

export function evaluateEquals(state) {
  if (state.hasError) return state

  const nextTokens = state.justEvaluated ? [] : [...state.tokens]
  if (nextTokens.length === 0) {
    const val = parseDisplayValue(state.display)
    if (!Number.isFinite(val)) return errorState(state, 'Invalid input')
    const fmt = formatNumber(val)
    return {
      ...state,
      display: fmt ?? 'Error',
      expression: state.lastUnaryExpr ? `${state.lastUnaryExpr} =` : `${fmt} =`,
      isNewEntry: true,
      justEvaluated: true,
      lastUnaryExpr: null,
    }
  }

  const last = nextTokens[nextTokens.length - 1]
  if (!last || last.type === 'op' || last.type === 'lparen') {
    const val = parseDisplayValue(state.display)
    if (!Number.isFinite(val)) return errorState(state, 'Invalid input')
    const label = state.lastUnaryExpr || formatNumber(val) || state.display
    nextTokens.push({ type: 'num', value: val, label })
  }

  // Auto-close any open parentheses before evaluating
  let open = countOpenParens(nextTokens)
  while (open > 0) {
    nextTokens.push({ type: 'rparen' })
    open -= 1
  }

  const res = evaluateTokens(nextTokens)
  if (res.error) {
    return errorState(state, res.error)
  }
  const formatted = formatNumber(res.value)
  if (formatted === null) {
    return errorState(state, 'Error')
  }

  return {
    ...state,
    display: formatted,
    expression: `${tokensToExpression(nextTokens)} =`,
    tokens: [],
    isNewEntry: true,
    justEvaluated: true,
    hasError: false,
    lastUnaryExpr: null,
  }
}

export function memoryAction(state, action) {
  if (state.hasError && action !== 'MC') return state
  const current = parseDisplayValue(state.display)

  switch (action) {
    case 'MC':
      return { ...state, memory: 0 }
    case 'MR':
      return {
        ...state,
        display: formatNumber(state.memory) ?? '0',
        isNewEntry: true,
        justEvaluated: false,
        hasError: false,
        lastUnaryExpr: null,
      }
    case 'MS':
      if (!Number.isFinite(current)) return state
      return { ...state, memory: current, isNewEntry: true }
    case 'M+':
      if (!Number.isFinite(current)) return state
      return { ...state, memory: state.memory + current, isNewEntry: true }
    case 'M-':
      if (!Number.isFinite(current)) return state
      return { ...state, memory: state.memory - current, isNewEntry: true }
    default:
      return state
  }
}
