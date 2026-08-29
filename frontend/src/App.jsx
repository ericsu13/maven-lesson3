import { useState, useRef, useLayoutEffect } from 'react'

// Turn "strategic_priorities" -> "Strategic Priorities"
const humanize = (key) =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

// A textarea that grows to fit its content (no inner scrollbars).
function AutoTextarea({ value, onChange, disabled, id }) {
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = `${el.scrollHeight}px`
    }
  }, [value])
  return (
    <textarea
      id={id}
      ref={ref}
      value={value}
      disabled={disabled}
      onChange={onChange}
    />
  )
}

function Spinner() {
  return <span className="spinner" aria-hidden="true" />
}

function Logo() {
  return (
    <span className="logo" aria-hidden="true">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
        <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
        <line x1="16.5" y1="16.5" x2="21" y2="21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <path d="M8 11.5l2 2 4-4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}

export default function App() {
  const [company, setCompany] = useState('')
  const [useMemory, setUseMemory] = useState(true)
  const [phase, setPhase] = useState('input') // input | analyzing | review | submitting | done
  const [steps, setSteps] = useState([])
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null) // { company, competitors, run_ts }
  const [plan, setPlan] = useState(null) // { field: string[] }
  const [submitResult, setSubmitResult] = useState(null)
  const [showModal, setShowModal] = useState(false)

  const analyze = async (e) => {
    e.preventDefault()
    if (!company.trim()) return
    setError(null)
    setSubmitResult(null)
    setSteps([])
    setPhase('analyzing')

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company: company.trim(), use_memory: useMemory }),
      })
      if (!res.ok || !res.body) throw new Error(`Server error (${res.status})`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let settled = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop()
        for (const part of parts) {
          const line = part.replace(/^data:\s?/, '').trim()
          if (!line) continue
          const evt = JSON.parse(line)
          if (evt.type === 'step') {
            setSteps((prev) => [...prev, evt.message])
          } else if (evt.type === 'result') {
            settled = true
            setResult(evt)
            setPlan(evt.plan)
            setPhase('review')
          } else if (evt.type === 'error') {
            settled = true
            setError(evt)
            setPhase('input')
          }
        }
      }
      if (!settled) {
        setError({ message: 'The analysis ended unexpectedly. Please try again.' })
        setPhase('input')
      }
    } catch (err) {
      setError({ message: err.message })
      setPhase('input')
    }
  }

  const updateField = (key, text) => {
    setPlan((prev) => ({ ...prev, [key]: text.split('\n') }))
  }

  const submit = async () => {
    setError(null)
    setPhase('submitting')
    const cleaned = Object.fromEntries(
      Object.entries(plan).map(([k, v]) => [k, v.map((s) => s.trim()).filter(Boolean)]),
    )
    try {
      const res = await fetch('/api/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company: result.company, plan: cleaned }),
      })
      if (!res.ok) throw new Error(`Server error (${res.status})`)
      const data = await res.json()
      if (data.status === 'PASS') {
        setSubmitResult(data)
        setShowModal(true)
        setPhase('done')
      } else {
        setError(data)
        setPhase('review')
      }
    } catch (err) {
      setError({ message: err.message })
      setPhase('review')
    }
  }

  const reset = () => {
    setPhase('input')
    setResult(null)
    setPlan(null)
    setSubmitResult(null)
    setError(null)
    setSteps([])
    setShowModal(false)
    setCompany('')
  }

  return (
    <div className="page">
      <header className="header">
        <h1>
          <Logo />
          <span className="title-text">Competitive Analysis Agent</span>
        </h1>
        <p className="subtitle">
          Research a company and its competitors, then publish an account plan to Salesforce.
        </p>
      </header>

      {(phase === 'input' || phase === 'analyzing') && (
        <form className="card search" onSubmit={analyze}>
          <label htmlFor="company">Company Name</label>
          <div className="search-row">
            <input
              id="company"
              type="text"
              placeholder="e.g. Salesforce"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              disabled={phase === 'analyzing'}
              autoFocus
            />
            <button type="submit" disabled={phase === 'analyzing' || !company.trim()}>
              {phase === 'analyzing' ? 'Analyzing…' : 'Analyze'}
            </button>
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={useMemory}
              onChange={(e) => setUseMemory(e.target.checked)}
              disabled={phase === 'analyzing'}
            />
            <span>
              Use memory (mem0) — persist research and synthesize from it. Uncheck to
              run in-memory only.
            </span>
          </label>
        </form>
      )}

      {phase === 'analyzing' && (
        <div className="card progress">
          <ul className="steplog">
            {steps.map((s, i) => (
              <li key={i} className={i === steps.length - 1 ? 'active' : 'done'}>
                <span className="bullet" />
                {s}
              </li>
            ))}
            <li className="working">
              <Spinner />
              Working…
            </li>
          </ul>
        </div>
      )}

      {error && phase !== 'done' && (
        <div className="card error">
          <strong>Analysis could not be completed</strong>
          <p>{error.message}</p>
          {error.stage && <span className="tag">stage: {error.stage}</span>}
        </div>
      )}

      {(phase === 'review' || phase === 'submitting' || phase === 'done') && plan && (
        <section className="card plan">
          <div className="plan-head">
            <h2>{result.company} — Account Plan</h2>
            {result.competitors?.length > 0 && (
              <div className="competitors">
                <span className="label">Competitors:</span>
                {result.competitors.map((c) => (
                  <span key={c} className="chip">
                    {c}
                  </span>
                ))}
              </div>
            )}
          </div>

          {phase !== 'done' && (
            <p className="hint">
              Review and edit each section below (one bullet per line), then publish to Salesforce.
            </p>
          )}

          <div className="fields">
            {Object.entries(plan).map(([key, values]) => (
              <div className="field" key={key}>
                <label htmlFor={key}>{humanize(key)}</label>
                <AutoTextarea
                  id={key}
                  value={values.join('\n')}
                  disabled={phase !== 'review'}
                  onChange={(e) => updateField(key, e.target.value)}
                />
              </div>
            ))}
          </div>

          {phase !== 'done' && (
            <div className="actions">
              <button className="secondary" onClick={reset} disabled={phase === 'submitting'}>
                Start over
              </button>
              <button onClick={submit} disabled={phase === 'submitting'}>
                {phase === 'submitting' ? 'Publishing…' : 'Publish to Salesforce'}
              </button>
            </div>
          )}
        </section>
      )}

      {showModal && submitResult && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-check">✓</div>
            <h3>Published to Salesforce</h3>
            <p>{submitResult.message}</p>
            {submitResult.details?.record_url && (
              <a
                className="modal-link"
                href={submitResult.details.record_url}
                target="_blank"
                rel="noreferrer"
              >
                Open the Account Plan record →
              </a>
            )}
            <div className="modal-actions">
              <button className="secondary" onClick={() => setShowModal(false)}>
                View plan
              </button>
              <button onClick={reset}>Analyze another company</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
