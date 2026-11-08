import { PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'stash'
const ROUTE = '/stash'
const BASE = `/api/plugins/${ID}`
const VERSION = '0.2.0'

// Which door this page actually reached the backend through. Surfaced in the UI:
// a wrong answer here is the difference between "the plugin is broken" and
// "the new file never loaded", and guessing between those costs hours.
const transport = { used: 'none' }

// --- api -----------------------------------------------------------------------------------

/** The app's own HTTP door. In a remote session the renderer is NOT the backend,
 *  so a relative fetch has nowhere to land ("Failed to fetch"); this bridge
 *  resolves the active backend, its auth and its profile for us. Older shells
 *  without the bridge fall back to fetch, which is correct when the app is local. */
function nativeApi() {
  const bridge = typeof window === 'undefined' ? null : window.hermesDesktop && window.hermesDesktop.api
  return typeof bridge === 'function' ? bridge.bind(window.hermesDesktop) : null
}

async function rest(path, opts = {}) {
  const method = opts.method || 'GET'
  const bridge = nativeApi()

  if (bridge) {
    transport.used = 'bridge'
    const payload = await bridge({ path: `${BASE}${path}`, method, body: opts.body })
    if (payload && payload.ok === false) {
      throw new Error(payload.error || 'request failed')
    }
    return payload || {}
  }

  transport.used = 'fetch'
  const response = await fetch(BASE + path, {
    method,
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined
  })
  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  if (!response.ok || (payload && payload.ok === false)) {
    throw new Error((payload && payload.error) || `${response.status} ${response.statusText}`)
  }
  return payload || {}
}

const api = {
  items: () => rest('/items'),
  add: text => rest('/add', { method: 'POST', body: { text } }),
  upload: (name, dataUrl) => rest('/upload', { method: 'POST', body: { name, dataUrl } }),
  status: (id, status) => rest('/status', { method: 'POST', body: { id, status } }),
  remove: id => rest('/delete', { method: 'POST', body: { id } })
}

// --- helpers -------------------------------------------------------------------------------

const MUTED = 'var(--muted-foreground, rgba(128,128,128,.9))'
const LINE = 'var(--border, rgba(128,128,128,.22))'
const FG = 'var(--foreground, #e8e8e8)'

function ago(seconds) {
  const delta = Date.now() / 1000 - (seconds || 0)
  if (delta < 90) return 'just now'
  if (delta < 3600) return `${Math.round(delta / 60)}m`
  if (delta < 86400) return `${Math.round(delta / 3600)}h`
  if (delta < 86400 * 7) return `${Math.round(delta / 86400)}d`
  return new Date((seconds || 0) * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

const KIND_GLYPH = { link: '↗', video: '▶', image: '▣', text: '≡' }
const KIND_TINT = {
  link: 'rgba(120,160,255,.16)',
  video: 'rgba(255,120,140,.16)',
  image: 'rgba(160,220,150,.16)',
  text: 'rgba(190,160,240,.16)'
}

const STATUS_LABEL = { new: 'New', reading: 'Reading', done: 'Done', dropped: 'Dropped' }
const NEXT_STATUS = { new: 'done', done: 'dropped', dropped: 'new', reading: 'done' }

function Pill({ status, onClick }) {
  const tone = {
    new: ['rgba(120,160,255,.20)', 'rgba(140,175,255,1)'],
    reading: ['rgba(230,190,110,.18)', 'rgba(230,190,110,1)'],
    done: ['rgba(130,210,150,.16)', 'rgba(130,210,150,1)'],
    dropped: ['rgba(150,150,150,.14)', MUTED]
  }[status] || ['transparent', MUTED]
  return jsx('button', {
    type: 'button',
    onClick,
    title: `mark ${NEXT_STATUS[status] || 'done'}`,
    style: {
      background: tone[0],
      color: tone[1],
      border: `1px solid ${LINE}`,
      borderRadius: 999,
      padding: '2px 9px',
      fontSize: 11,
      lineHeight: '16px',
      cursor: 'pointer',
      fontFamily: 'inherit'
    },
    children: STATUS_LABEL[status] || status
  })
}

function Thumb({ item }) {
  const [broken, setBroken] = useState(false)
  const size = 42
  if (item.thumb && !broken) {
    return jsx('img', {
      src: item.thumb,
      alt: '',
      referrerPolicy: 'no-referrer',
      loading: 'lazy',
      onError: () => setBroken(true),
      style: {
        width: size,
        height: size,
        borderRadius: 8,
        objectFit: 'cover',
        flex: 'none',
        border: `1px solid ${LINE}`,
        background: 'rgba(128,128,128,.10)'
      }
    })
  }
  return jsx('div', {
    style: {
      width: size,
      height: size,
      borderRadius: 8,
      flex: 'none',
      display: 'grid',
      placeItems: 'center',
      fontSize: 15,
      color: MUTED,
      background: KIND_TINT[item.kind] || KIND_TINT.text,
      border: `1px solid ${LINE}`
    },
    children: KIND_GLYPH[item.kind] || '≡'
  })
}

function Row({ item, onStatus, onDelete }) {
  const meta = [item.host, ago(item.added), item.kind === 'text' ? 'note' : item.kind]
    .filter(Boolean)
    .join(' · ')
  const open = () => {
    if (item.url && host && typeof host.openExternal === 'function') {
      host.openExternal(item.url)
    }
  }
  return jsxs('li', {
    style: {
      display: 'flex',
      gap: 12,
      alignItems: 'flex-start',
      padding: '10px 12px',
      border: `1px solid ${LINE}`,
      borderRadius: 10,
      background: 'var(--card, rgba(128,128,128,.05))',
      opacity: item.status === 'dropped' ? 0.55 : 1
    },
    children: [
      jsx(Thumb, { key: 'thumb', item }),
      jsxs('div', { key: 'body', style: { minWidth: 0, flex: 1 }, children: [
        jsx('div', {
          onClick: open,
          title: item.url || item.title,
          style: {
            color: FG,
            fontSize: 13,
            fontWeight: 500,
            lineHeight: '18px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            textDecoration: item.status === 'done' ? 'line-through' : 'none',
            cursor: item.url ? 'pointer' : 'default'
          },
          children: item.title || item.url || 'untitled'
        }),
        jsx('div', {
          style: { color: MUTED, fontSize: 11, marginTop: 2, display: 'flex', gap: 6, flexWrap: 'wrap' },
          children: meta
        }),
        item.note
          ? jsx('div', { style: { color: MUTED, fontSize: 12, marginTop: 5, lineHeight: '17px' }, children: item.note })
          : null
      ] }),
      jsxs('div', { key: 'actions', style: { display: 'flex', gap: 6, alignItems: 'center', flex: 'none' }, children: [
        jsx(Pill, { key: 'pill', status: item.status, onClick: () => onStatus(item, NEXT_STATUS[item.status] || 'done') }),
        jsx('button', {
          key: 'del',
          type: 'button',
          title: 'delete',
          onClick: () => onDelete(item),
          style: {
            background: 'transparent',
            border: `1px solid ${LINE}`,
            color: MUTED,
            borderRadius: 8,
            width: 24,
            height: 24,
            cursor: 'pointer',
            fontSize: 13,
            lineHeight: '20px',
            padding: 0,
            fontFamily: 'inherit'
          },
          children: '×'
        })
      ] })
    ]
  })
}

// --- page ----------------------------------------------------------------------------------

function StashPage() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('all')
  const [dragging, setDragging] = useState(false)
  const [flash, setFlash] = useState('')
  const box = useRef(null)
  const fileInput = useRef(null)

  const refresh = useCallback(async () => {
    try {
      const payload = await api.items()
      setItems(payload.items || [])
      setError('')
    } catch (exc) {
      setError(exc.message || String(exc))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const say = message => {
    setFlash(message)
    window.setTimeout(() => setFlash(current => (current === message ? '' : current)), 2200)
  }

  const save = async () => {
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true)
    try {
      const payload = await api.add(text)
      setItems(payload.items || [])
      setDraft('')
      say(`${(payload.added || []).length} saved`)
      // titles land a moment later from the enrich pass
      window.setTimeout(refresh, 1600)
    } catch (exc) {
      setError(exc.message || String(exc))
    } finally {
      setBusy(false)
    }
  }

  const upload = async file => {
    if (!file) return
    setBusy(true)
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result || ''))
        reader.onerror = () => reject(new Error('could not read that file'))
        reader.readAsDataURL(file)
      })
      const payload = await api.upload(file.name || 'pasted.png', dataUrl)
      setItems(payload.items || [])
      say('image saved')
    } catch (exc) {
      setError(exc.message || String(exc))
    } finally {
      setBusy(false)
    }
  }

  const onStatus = async (item, status) => {
    const optimistic = items.map(row => (row.id === item.id ? { ...row, status } : row))
    setItems(optimistic)
    try {
      const payload = await api.status(item.id, status)
      setItems(payload.items || optimistic)
    } catch (exc) {
      setError(exc.message || String(exc))
      refresh()
    }
  }

  const onDelete = async item => {
    const optimistic = items.filter(row => row.id !== item.id)
    setItems(optimistic)
    try {
      await api.remove(item.id)
    } catch (exc) {
      setError(exc.message || String(exc))
      refresh()
    }
  }

  const shown = useMemo(() => {
    const rows = items.slice().sort((a, b) => (b.added || 0) - (a.added || 0))
    if (filter === 'all') return rows.filter(row => row.status !== 'dropped')
    if (filter === 'open') return rows.filter(row => row.status === 'new' || row.status === 'reading')
    return rows.filter(row => row.status === filter)
  }, [items, filter])

  const counts = useMemo(() => {
    const base = { all: 0, open: 0, done: 0, dropped: 0 }
    for (const row of items) {
      if (row.status !== 'dropped') base.all += 1
      if (row.status === 'new' || row.status === 'reading') base.open += 1
      if (row.status === 'done') base.done += 1
      if (row.status === 'dropped') base.dropped += 1
    }
    return base
  }, [items])

  const chips = [
    ['all', `All ${counts.all}`],
    ['open', `Open ${counts.open}`],
    ['done', `Done ${counts.done}`],
    ['dropped', `Dropped ${counts.dropped}`]
  ]

  return jsxs('div', {
    style: {
      height: '100%',
      overflowY: 'auto',
      padding: '28px 32px 64px',
      color: FG,
      fontFamily: 'inherit',
      background: 'transparent'
    },
    children: [
      jsxs('header', { key: 'head', style: { marginBottom: 18 }, children: [
        jsx('h1', {
          style: { margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em' },
          children: 'Stash'
        }),
        jsx('p', {
          style: { margin: '4px 0 0', color: MUTED, fontSize: 12.5 },
          children: 'Anything you saw and want to come back to. Nothing here touches a chat until you ask for it.'
        })
      ] }),
      jsxs('div', {
        key: 'capture',
        onDragOver: event => {
          event.preventDefault()
          setDragging(true)
        },
        onDragLeave: () => setDragging(false),
        onDrop: event => {
          event.preventDefault()
          setDragging(false)
          const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]
          if (file) upload(file)
        },
        style: {
          border: `1px ${dragging ? 'dashed' : 'solid'} ${dragging ? 'var(--accent, #7aa2ff)' : LINE}`,
          background: dragging ? 'rgba(120,160,255,.06)' : 'var(--card, rgba(128,128,128,.05))',
          borderRadius: 12,
          padding: 12,
          marginBottom: 16
        },
        children: [
          jsx('textarea', {
            key: 'ta',
            ref: box,
            value: draft,
            onChange: event => setDraft(event.target.value),
            onPaste: event => {
              const files = event.clipboardData && event.clipboardData.files
              if (files && files.length && files[0].type.startsWith('image/')) {
                event.preventDefault()
                upload(files[0])
              }
            },
            onKeyDown: event => {
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                event.preventDefault()
                save()
              }
            },
            placeholder:
              'Paste a link, a YouTube url, an image, or a few lines — one item per line. ⌘↵ to save.',
            rows: 3,
            style: {
              width: '100%',
              resize: 'vertical',
              minHeight: 62,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: FG,
              fontSize: 13,
              lineHeight: '20px',
              fontFamily: 'inherit',
              boxSizing: 'border-box'
            }
          }),
          jsxs('div', {
            key: 'bar',
            style: { display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 },
            children: [
              jsx('button', {
                key: 'save',
                type: 'button',
                onClick: save,
                disabled: busy || !draft.trim(),
                style: {
                  background: draft.trim() && !busy ? 'var(--accent, #7aa2ff)' : 'rgba(128,128,128,.18)',
                  color: draft.trim() && !busy ? '#0b0b0d' : MUTED,
                  border: 'none',
                  borderRadius: 8,
                  padding: '6px 14px',
                  fontSize: 12.5,
                  fontWeight: 600,
                  cursor: draft.trim() && !busy ? 'pointer' : 'default',
                  fontFamily: 'inherit'
                },
                children: busy ? 'Saving…' : 'Save'
              }),
              jsx('button', {
                key: 'file',
                type: 'button',
                onClick: () => fileInput.current && fileInput.current.click(),
                style: {
                  background: 'transparent',
                  border: `1px solid ${LINE}`,
                  color: MUTED,
                  borderRadius: 8,
                  padding: '6px 10px',
                  fontSize: 12,
                  cursor: 'pointer',
                  fontFamily: 'inherit'
                },
                children: 'Add image…'
              }),
              jsx('input', {
                key: 'input',
                ref: fileInput,
                type: 'file',
                accept: 'image/*',
                onChange: event => {
                  const file = event.target.files && event.target.files[0]
                  upload(file)
                  event.target.value = ''
                },
                style: { display: 'none' }
              }),
              jsx('span', { key: 'flash', style: { color: MUTED, fontSize: 11.5 }, children: flash })
            ]
          })
        ]
      }),
      error
        ? jsx('div', {
            key: 'err',
            style: {
              border: '1px solid rgba(220,120,120,.5)',
              background: 'rgba(220,120,120,.10)',
              color: 'rgba(240,160,160,1)',
              borderRadius: 8,
              padding: '8px 10px',
              fontSize: 12,
              marginBottom: 14
            },
            children: `${error}  ·  via ${transport.used}  ·  bridge ${nativeApi() ? 'present' : 'absent'}  ·  v${VERSION}`
          })
        : null,
      jsx('div', {
        key: 'chips',
        style: { display: 'flex', gap: 6, marginBottom: 12 },
        children: chips.map(([key, label]) =>
          jsx('button', {
            type: 'button',
            onClick: () => setFilter(key),
            style: {
              background: filter === key ? 'rgba(128,128,128,.18)' : 'transparent',
              border: `1px solid ${LINE}`,
              color: filter === key ? FG : MUTED,
              borderRadius: 999,
              padding: '4px 11px',
              fontSize: 11.5,
              cursor: 'pointer',
              fontFamily: 'inherit'
            },
            children: label
          }, key)
        )
      }),
      jsx('div', {
        key: 'footer',
        style: { marginTop: 18, color: MUTED, fontSize: 10.5, opacity: 0.75 },
        children: `v${VERSION} · backend via ${transport.used} · bridge ${nativeApi() ? 'present' : 'absent'}`
      }),
      loading
        ? jsx('div', { key: 'loading', style: { color: MUTED, fontSize: 12.5 }, children: 'loading…' })
        : shown.length === 0
          ? jsx('div', {
              key: 'empty',
              style: {
                border: `1px dashed ${LINE}`,
                borderRadius: 10,
                padding: '28px 16px',
                textAlign: 'center',
                color: MUTED,
                fontSize: 12.5
              },
              children: items.length ? 'nothing in this filter' : 'nothing saved yet — drop a link in the box above'
            })
          : jsx('ul', {
              key: 'list',
              style: { listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 8 },
              children: shown.map(item =>
                jsx(Row, { item, onStatus, onDelete }, item.id)
              )
            })
    ]
  })
}

export default {
  id: ID,
  name: 'Stash',
  register(ctx) {
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(StashPage, {}) })
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      order: 87,
      data: { path: ROUTE, label: 'Stash', codicon: 'inbox' }
    })
    ctx.register({
      id: 'open',
      area: PALETTE_AREA,
      data: {
        id: 'stash.open',
        label: 'Open Stash',
        keywords: ['inbox', 'save', 'later', 'bookmark', 'capture'],
        run: () => host.navigate(ROUTE)
      }
    })
  }
}
