window.__ModuleLoader__.load({ id: 'dsh-dafeiyu', factory: (require) => {
  const module = { exports: {} }
  const exports = module.exports
  const React = require('react')
  const { useEffect, useState, useCallback } = React
  const CONFIG_ENDPOINT = '/plugins/dsh-dafeiyu/config'
  const ASSET_PREFIX = '/plugins/dsh-dafeiyu/assets'
  const OPEN_FOLDER_ENDPOINT = '/plugins/dsh-dafeiyu/open-folder'

  const cardStyle = {
    border: '1px solid var(--border-color, #d8d8d8)', borderRadius: 12,
    padding: 16, background: 'var(--surface-color, transparent)', display: 'grid', gap: 14,
  }
  const rowStyle = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 20 }
  const selectStyle = { minWidth: 120, padding: '6px 10px', borderRadius: 8 }
  const buttonStyle = {
    padding: '5px 12px', borderRadius: 8, cursor: 'pointer', flexShrink: 0,
    border: '1px solid var(--border-color, #d8d8d8)',
    background: 'var(--surface-color, transparent)', color: 'inherit',
  }

  function Field({ label, hint, children }) {
    return React.createElement('label', { style: rowStyle },
      React.createElement('span', null,
        React.createElement('span', { style: { display: 'block', fontWeight: 600 } }, label),
        React.createElement('small', { style: { display: 'block', opacity: 0.65, marginTop: 3 } }, hint),
      ),
      children,
    )
  }

  // 拨纽（switch），取代复选框，用于启用类设置。
  function Switch({ checked, disabled, onChange, label }) {
    return React.createElement('button', {
      type: 'button',
      role: 'switch',
      'aria-checked': checked,
      'aria-label': label,
      disabled,
      onClick: () => onChange(!checked),
      style: {
        display: 'inline-flex', alignItems: 'center', flexShrink: 0,
        width: 46, height: 26, padding: 3, borderRadius: 13,
        border: '1px solid var(--border-color, #0000002e)',
        background: checked ? 'var(--primary-color, #4c8dff)' : 'var(--border-color, #9aa0a6)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1,
        transition: 'background-color .18s ease',
        outline: 'none',
      },
    }, React.createElement('span', {
      style: {
        display: 'block', width: 18, height: 18, borderRadius: '50%',
        background: '#fff', boxShadow: '0 1px 2px rgba(0,0,0,.4)',
        transform: checked ? 'translateX(20px)' : 'translateX(0)',
        transition: 'transform .18s ease',
      },
    }))
  }

  // ---- 动作循环照片墙：由 pet-manifest.json 的 photoWall 契约驱动 ----
  function buildGroups(manifest) {
    const clips = manifest?.clips ?? {}
    const wall = Array.isArray(manifest?.photoWall) ? manifest.photoWall : []
    const groups = []
    for (const entry of wall) {
      const frames = []
      const seen = new Set()
      for (const clipId of entry.clips ?? []) {
        const clip = clips[clipId]
        if (!clip) continue
        for (const frame of clip.frames ?? []) {
          if (seen.has(frame)) continue
          seen.add(frame)
          frames.push({ frame, clip: clipId })
        }
      }
      if (frames.length === 0) continue
      const folder = entry.folder ?? (frames[0].frame.includes('/') ? frames[0].frame.split('/')[0] : '.')
      groups.push({ id: entry.id, label: entry.label ?? entry.id, folder, frames })
    }
    return groups
  }

  function BigFishPetSection() {
    const [status, setStatus] = useState('loading')
    const [value, setValue] = useState({})
    const [busy, setBusy] = useState(false)
    const [groups, setGroups] = useState([])
    const [wallStatus, setWallStatus] = useState('loading')
    const [openError, setOpenError] = useState(null)
    const writable = status === 'ready' && !busy

    const loadConfig = useCallback(async () => {
      try {
        const response = await fetch(CONFIG_ENDPOINT, { cache: 'no-store' })
        if (!response.ok) throw new Error(`settings request failed: ${response.status}`)
        setValue(await response.json())
        setStatus('ready')
      } catch {
        setStatus('unavailable')
      }
    }, [])

    const loadWall = useCallback(async () => {
      setWallStatus('loading')
      try {
        const response = await fetch(`${ASSET_PREFIX}/pet-manifest.json`, { cache: 'no-store' })
        if (!response.ok) throw new Error(`manifest request failed: ${response.status}`)
        setGroups(buildGroups(await response.json()))
        setWallStatus('ready')
      } catch {
        setWallStatus('error')
      }
    }, [])

    useEffect(() => { loadConfig(); loadWall() }, [loadConfig, loadWall])

    const write = async (field, next) => {
      setBusy(true)
      try {
        const response = await fetch(CONFIG_ENDPOINT, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ [field]: next }),
        })
        if (!response.ok) throw new Error(`settings write failed: ${response.status}`)
        setValue(await response.json())
        setStatus('ready')
      } catch {
        setStatus('unavailable')
      } finally {
        setBusy(false)
      }
    }

    // 打开文件夹走插件自有 host 路由（explorer.exe），刻意不用
    // ctx.workspaces.openPath——dsh-better-sidebar 会替换该方法并把打开
    // 操作劫持到它的侧边栏编辑器（编辑器无法显示目录）。
    const onOpen = async (folder) => {
      setOpenError(null)
      try {
        const response = await fetch(`${OPEN_FOLDER_ENDPOINT}?folder=${encodeURIComponent(folder)}`, { cache: 'no-store' })
        if (!response.ok) {
          if (response.status === 404) throw new Error('打开文件夹接口未就绪，请重启 DSH Host 后重试')
          const body = await response.json().catch(() => null)
          throw new Error(body?.error ?? `request failed: ${response.status}`)
        }
      } catch (error) {
        setOpenError(error instanceof Error ? error.message : String(error))
      }
    }

    const wallHeader = React.createElement('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 } },
      React.createElement('strong', { style: { fontSize: 15 } }, '动作循环照片墙'),
      React.createElement('button', {
        type: 'button',
        onClick: () => { loadConfig(); loadWall() },
        style: buttonStyle,
        title: '编辑 manifest 或替换图片后重新加载',
      }, '重新加载'),
    )

    const wallBody = wallStatus === 'error'
      ? React.createElement('span', { role: 'status' }, '照片墙加载失败：无法读取 pet-manifest.json，请确认 DSH Host 已重启。')
      : wallStatus === 'loading'
      ? React.createElement('span', null, '正在加载照片墙…')
      : groups.length === 0
      ? React.createElement('span', null, 'manifest 中还没有配置 photoWall 分组。')
      : groups.map((group) => React.createElement('div', {
          key: group.id,
          style: { border: '1px solid var(--border-color, #d8d8d8)', borderRadius: 10, padding: 10, display: 'grid', gap: 8 },
        },
        React.createElement('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 } },
          React.createElement('strong', null, group.label),
          React.createElement('button', {
            type: 'button',
            onClick: () => void onOpen(group.folder),
            title: `pet/${group.folder}`,
            style: buttonStyle,
          }, '打开文件夹'),
        ),
        React.createElement('div', { style: { display: 'flex', gap: 8, overflowX: 'auto', padding: '4px 2px 8px' } },
          group.frames.map(({ frame, clip }, index) =>
            React.createElement('figure', { key: `${frame}-${index}`, style: { margin: 0, flexShrink: 0, textAlign: 'center' } },
              React.createElement('img', {
                src: `${ASSET_PREFIX}/pet/${frame}`,
                alt: frame,
                title: `${clip} · ${frame}`,
                loading: 'lazy',
                style: { height: 72, width: 'auto', borderRadius: 6, border: '1px solid var(--border-color, #d8d8d8)', background: '#fff' },
                onError: (event) => { event.currentTarget.style.visibility = 'hidden' },
              }),
              React.createElement('figcaption', {
                style: { fontSize: 10, opacity: 0.65, marginTop: 3, maxWidth: 88, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
              }, frame.split('/').pop()),
            ),
          ),
        ),
      ))

    return React.createElement('div', { style: { display: 'grid', gap: 20, maxWidth: 780 } },
      React.createElement('div', { style: { textAlign: 'center' } },
        React.createElement('img', {
          src: `${ASSET_PREFIX}/pet/working/stay.png`,
          alt: '大肥鱼立绘（stay 静默状态）',
          style: { width: 260, height: 'auto', borderRadius: 12 },
        }),
        React.createElement('p', { style: { margin: '8px 0 0', opacity: 0.72 } }, 'stay 静默状态'),
      ),
      React.createElement('label', { style: rowStyle },
        React.createElement('span', null,
          React.createElement('span', { style: { display: 'block', fontWeight: 600 } }, '启用桌宠'),
          React.createElement('small', { style: { display: 'block', opacity: 0.65, marginTop: 3 } }, '关闭后立即退出；重新开启无需单独启动程序。'),
        ),
        React.createElement(Switch, {
          checked: value.enabled !== false, disabled: !writable, label: '启用桌宠',
          onChange: (next) => void write('enabled', next),
        }),
      ),
      status === 'unavailable'
        ? React.createElement('span', { role: 'status' }, '桌宠设置尚未连接到 DSH Host。')
        : status === 'loading'
        ? React.createElement('span', null, '正在读取设置…')
        : React.createElement('div', { style: cardStyle },
            React.createElement(Field, { label: '角色大小', hint: `${Math.round((value.scale ?? 1) * 100)}%` },
              React.createElement('input', {
                type: 'range', min: 0.7, max: 1.4, step: 0.05, value: value.scale ?? 1, disabled: !writable,
                onChange: (event) => void write('scale', Number(event.target.value)),
              }),
            ),
            React.createElement(Field, { label: '活跃程度', hint: '控制空闲时微动作的出现频率。' },
              React.createElement('select', {
                value: value.activityLevel ?? 'normal', disabled: !writable, style: selectStyle,
                onChange: (event) => void write('activityLevel', event.target.value),
              },
              React.createElement('option', { value: 'quiet' }, '安静'),
              React.createElement('option', { value: 'normal' }, '标准'),
              React.createElement('option', { value: 'lively' }, '活泼')),
            ),
            React.createElement(Field, { label: '减少动态效果', hint: '减少走动、循环帧和程序化晃动。' },
              React.createElement('input', {
                type: 'checkbox', checked: value.reducedMotion === true, disabled: !writable,
                onChange: (event) => void write('reducedMotion', event.target.checked),
              }),
            ),
            React.createElement(Field, { label: '响应子 Agent', hint: '默认只跟随顶层任务，避免状态过度跳动。' },
              React.createElement('input', {
                type: 'checkbox', checked: value.includeSubagents === true, disabled: !writable,
                onChange: (event) => void write('includeSubagents', event.target.checked),
              }),
            ),
            busy ? React.createElement('small', { role: 'status' }, '正在保存…') : null,
          ),
      React.createElement('section', { style: { display: 'grid', gap: 10 } },
        wallHeader,
        React.createElement('small', { style: { opacity: 0.65 } }, '照片墙由 pet-manifest.json 的 photoWall 契约驱动；新增动作模组或替换图片后点击“重新加载”即可热更新。每帧文件名与桌面调试日志一一对应。'),
        wallBody,
        openError ? React.createElement('small', { role: 'status', style: { color: '#c0392b' } }, `打开文件夹失败：${openError}`) : null,
      ),
    )
  }

  function apply(ctx) {
    ctx.slots.inject('settings.section', () => ctx.slots.register({
      name: 'settings.section', id: 'dsh-dafeiyu', order: 25, label: '桌宠',
      inject: () => ({}),
    }, BigFishPetSection))
  }

  module.exports = {
    name: 'dsh-dafeiyu-client',
    inject: ['slots'],
    apply,
  }
  return module.exports
} })
