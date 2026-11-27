import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

const UPRIGHT_BASES = new Set([
  // Common SI / bioprocess / lab units
  'L', 'm', 's', 'g', 'h', 'W', 'J', 'Pa', 'Hz',
  'mol', 'cm', 'mm', 'nm', 'um', 'pm', 'km', 'kg', 'mg', 'ug', 'ng', 'pg',
  'mL', 'uL', 'dL', 'min', 'hr', 'rad', 'kcal', 'kJ', 'Da', 'kDa', 'bp', 'kb', 'Mb',
  // Chemical elements & common groups
  'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne',
  'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl', 'Ar', 'Ca', 'Ti', 'Cr', 'Mn', 'Fe',
  'Co', 'Ni', 'Cu', 'Zn', 'Br', 'I', 'CO', 'NH', 'NO', 'SO', 'PO', 'OH', 'CN',
])

const LATEX_COMMANDS = [
  'Delta', 'delta', 'alpha', 'beta', 'gamma', 'Gamma', 'lambda', 'Lambda',
  'mu', 'nu', 'pi', 'Pi', 'sigma', 'Sigma', 'theta', 'Theta',
  'omega', 'Omega', 'phi', 'Phi', 'psi', 'Psi', 'rho', 'tau',
  'epsilon', 'varepsilon', 'kappa', 'chi', 'eta', 'zeta', 'xi',
  'rightarrow', 'leftarrow', 'leftrightarrow', 'Rightarrow', 'Leftarrow',
  'times', 'cdot', 'div', 'pm', 'mp', 'leq', 'geq', 'le', 'ge',
  'neq', 'approx', 'equiv', 'propto', 'infty', 'partial', 'nabla',
  'circ', 'degree', 'sim',
].join('|')

// Matches protected spans first: fenced code, inline code, $$...$$, \[...\], $...$, \(...\)
const PROTECTED_TOKEN_RE =
  /(```[\s\S]*?```|`[^`\n]+`|(?<!\\)\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|(?<!\\)\$(?!\s)(?:\\.|[^$\n])*?(?<!\s)(?<!\\)\$|\\\([\s\S]*?\\\))/g

const PLAIN_MATH_RE = new RegExp(
  [
    // 1. Explicit \mathrm{...}, \text{...}, \dfrac{...}{...}, \frac{...}{...}
    String.raw`\\(?:mathrm|text)\{[^{}]*\}`,
    String.raw`\\d?frac\{[^{}]*\}\{[^{}]*\}`,
    // 2. Scientific notation like 1.15 \times 10^{3}\;g or 1.31\times10^{3}
    String.raw`\d+(?:\.\d+)?\s*\\times\s*10\^(?:\{[^}]+\}|[+-]?\d+)(?:\s*\\;\s*[A-Za-z]+(?:\^(?:\{[^}]+\}|[+-]?\d+))?)?`,
    // 3. Chemical formulas with numeric subscripts (e.g. H_2O, CO_2, CO_{2}, O_2, C_6H_{12}O_6, SO_4^{2-})
    String.raw`\b(?:[A-Z][a-z]?)+_(?:\{\d+\}|\d+)(?:[A-Z][a-z]?(?:_(?:\{\d+\}|\d+))?)*(?:\^(?:\{[^{}]+\}|[+-]?\d+[+-]?|[+-]))?(?![A-Za-z0-9_])`,
    // 4. Braced super/subscripts (e.g. L^{-1}, Mn^{2+}, Fe^{2+}, 10^{-6}, K_{m}, V_{max}, mol^{-1})
    String.raw`(?<![A-Za-z0-9_\\])([A-Za-z0-9]+)((?:_\{[^{}]+\}|\^\{[^{}]+\})+)`,
    // 5. Unbraced numeric/charge superscripts (e.g. m^3, cm^2, s^-1, 10^-5, Ca^2+)
    String.raw`(?<![A-Za-z0-9_\\])([A-Za-z]+|\d+(?:\.\d+)?)\^([+-]?\d+[+-]?|[+-])(?![A-Za-z0-9_{])`,
    // 6. Specific bioprocess/short numeric subscripts without touching snake_case words
    String.raw`(?<![A-Za-z0-9_\\])(k_La)\b`,
    String.raw`(?<![A-Za-z0-9_\\])([A-Za-z])_(\d+)(?![A-Za-z0-9_])`,
    // 7. \Delta G / \Delta\Delta G
    String.raw`\\Delta(?:\\Delta)?\s*[A-Za-z]\b`,
    // 8. Stray bare LaTeX commands
    String.raw`\\(?:${LATEX_COMMANDS})\b`,
  ]
    .map((p) => `(?:${p})`)
    .join('|'),
  'g'
)

function formatBase(base) {
  if (/^\d+(?:\.\d+)?$/.test(base)) return base
  if (UPRIGHT_BASES.has(base)) return `\\mathrm{${base}}`
  if (base.length >= 2 && /^[A-Za-z]+$/.test(base)) return `\\mathrm{${base}}`
  return base
}

function normalizePlainTextSegment(segment) {
  // Unescape LLM-escaped numeric chemical subscripts like CO\_2 -> CO_2
  const cleaned = segment
    .replace(/\b([A-Z][a-z]?)\\_(\d+)\b/g, '$1_$2')
    .replace(/\\;/g, ' ')

  return cleaned.replace(PLAIN_MATH_RE, (match, ...args) => {
    // Identify which capture group matched by inspecting full match structure
    // 1/2/3/7/8 have direct replacements; 4, 5, 6 have captured bases/exponents

    // Braced super/subscripts (group 4: base, suffixes)
    const bracedMatch = /^([A-Za-z0-9]+)((?:_\{[^{}]+\}|\^\{[^{}]+\})+)$/.exec(match)
    if (bracedMatch) {
      const [, base, suffixes] = bracedMatch
      // Chemical formula like CO_{2}
      if (/^(?:[A-Z][a-z]?)+_\{\d+\}/.test(match)) {
        return `$\\mathrm{${match}}$`
      }
      return `$${formatBase(base)}${suffixes}$`
    }

    // Unbraced superscript (group 5: base^exp)
    const supMatch = /^([A-Za-z]+|\d+(?:\.\d+)?)\^([+-]?\d+[+-]?|[+-])$/.exec(match)
    if (supMatch) {
      const [, base, exp] = supMatch
      return `$${formatBase(base)}^{${exp}}$`
    }

    // Chemical formula with unbraced numeric subscripts (e.g. H_2O, CO_2, O_2, SO_4^{2-})
    if (/^(?:[A-Z][a-z]?)+_(?:\{\d+\}|\d+)/.test(match)) {
      return `$\\mathrm{${match}}$`
    }

    // Specific k_La
    if (match === 'k_La') {
      return '$k_{L}a$'
    }

    // Single-letter numeric subscript (e.g. x_1, t_0)
    const singleSub = /^([A-Za-z])_(\d+)$/.exec(match)
    if (singleSub) {
      return `$${singleSub[1]}_{${singleSub[2]}}$`
    }

    // Scientific notation with optional trailing \;unit
    if (match.includes('\\times')) {
      const normalized = match.replace(/\s*\\;\s*([A-Za-z]+.*)$/, ' \\mathrm{$1}')
      return `$${normalized.trim()}$`
    }

    // Explicit \mathrm{...}, \text{...}, \frac{...}, \Delta G, or bare \command
    return `$${match.trim()}$`
  })
}

export function normalizeMath(text) {
  if (!text) return ''
  const input = String(text)
  const parts = input.split(PROTECTED_TOKEN_RE)

  return parts
    .map((part, idx) => {
      if (!part) return ''
      // Odd indices are captured protected tokens from split(capturingRegex)
      if (idx % 2 === 1) {
        if (part.startsWith('\\[') && part.endsWith('\\]')) {
          return `$$${part.slice(2, -2)}$$`
        }
        if (part.startsWith('\\(') && part.endsWith('\\)')) {
          return `$${part.slice(2, -2)}$`
        }
        return part
      }
      return normalizePlainTextSegment(part)
    })
    .join('')
}

export default function MarkdownMath({ content, className = '', inline = false }) {
  const normalized = normalizeMath(content)

  return (
    <div
      className={`markdown-math text-slate-800 dark:text-slate-100 leading-relaxed ${
        inline ? 'inline-block' : ''
      } ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          p: ({ children }) =>
            inline ? (
              <span>{children}</span>
            ) : (
              <p className="mb-3 last:mb-0 whitespace-pre-line">{children}</p>
            ),
          ul: ({ children }) => (
            <ul className="list-disc pl-6 mb-3 space-y-1.5 last:mb-0">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-6 mb-3 space-y-1.5 last:mb-0">{children}</ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-slate-900 dark:text-white">
              {children}
            </strong>
          ),
          code: ({ children, className: codeCls }) => {
            const isBlock = Boolean(codeCls)
            return isBlock ? (
              <pre className="my-2 overflow-x-auto rounded-lg bg-slate-100 dark:bg-slate-900 p-3 text-xs font-mono border border-slate-200 dark:border-slate-800">
                <code>{children}</code>
              </pre>
            ) : (
              <code className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-xs font-mono text-indigo-700 dark:text-indigo-300">
                {children}
              </code>
            )
          },
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-indigo-400 dark:border-indigo-500 pl-3 my-2 text-slate-600 dark:text-slate-300 italic">
              {children}
            </blockquote>
          ),
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  )
}
