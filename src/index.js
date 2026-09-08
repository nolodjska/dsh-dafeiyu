import Schema from '@deepseek-ai/schemastery'
import { readFile, stat } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { dirname, normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CompanionReducer } from './companion-reducer.js'
import { HelperProcess } from './helper-process.js'
import {
  CompanionMessageKind,
  CompanionState,
  createMessage,
} from './protocol.js'

export const name = 'dsh-dafeiyu'
export const inject = ['sessions']
export const CONFIG_ENDPOINT = '/plugins/dsh-dafeiyu/config'
export const ASSET_PREFIX = '/plugins/dsh-dafeiyu/assets'
export const OPEN_FOLDER_ENDPOINT = '/plugins/dsh-dafeiyu/open-folder'
const assetRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'assets')
const PET_ASSET_DIR = resolve(assetRoot, 'pet')
export const Config = Schema.object({
  enabled: Schema.boolean().default(true).description('启用桌面大肥鱼'),
  scale: Schema.number().min(0.7).max(1.4).step(0.05).default(1).role('slider').description('角色大小'),
  activityLevel: Schema.union([
    Schema.const('quiet').description('安静'),
    Schema.const('normal').description('标准'),
    Schema.const('lively').description('活泼'),
  ]).default('normal').description('空闲微动作频率'),
  reducedMotion: Schema.boolean().default(false).description('减少走动、循环帧和程序化晃动'),
  includeSubagents: Schema.boolean().default(false).description('允许子 Agent 抢占宠物状态'),
}).description('由 DeepSeek Harness 状态驱动的桌面大肥鱼伴侣')

const defaults = Object.freeze({
  enabled: true,
  scale: 1,
  activityLevel: 'normal',
  reducedMotion: false,
  includeSubagents: false,
})

function publicConfig(config = {}) {
  return {
    enabled: config.enabled ?? defaults.enabled,
    scale: config.scale ?? defaults.scale,
    activityLevel: config.activityLevel ?? defaults.activityLevel,
    reducedMotion: config.reducedMotion ?? defaults.reducedMotion,
    includeSubagents: config.includeSubagents ?? defaults.includeSubagents,
  }
}

function localSettingsScope(value) {
  return {
    get: () => value,
    watch: () => () => {},
  }
}

function jsonResponse(res, status, body) {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(payload),
  })
  res.end(payload)
}

function isLoopback(address) {
  return address === '127.0.0.1' || address === '::1' || address === '::ffff:127.0.0.1'
}

async function readPatch(req) {
  const chunks = []
  let bytes = 0
  for await (const chunk of req) {
    bytes += chunk.length
    if (bytes > 8192) throw new Error('request body is too large')
    chunks.push(chunk)
  }
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('patch must be an object')
  const allowed = new Set(Object.keys(defaults))
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new Error('patch contains an unknown setting')
  return value
}

export function createConfigHandler(settings) {
  return async (req, res) => {
    if (!isLoopback(req.socket?.remoteAddress)) {
      jsonResponse(res, 403, { error: 'local access only' })
      return
    }
    const origin = req.headers?.origin
    if (origin) {
      let originHost
      try { originHost = new URL(origin).host } catch {}
      if (!originHost || originHost !== req.headers.host) {
        jsonResponse(res, 403, { error: 'origin mismatch' })
        return
      }
    }
    if (req.method === 'GET') {
      jsonResponse(res, 200, settings.get())
      return
    }
    if (req.method !== 'PATCH') {
      jsonResponse(res, 405, { error: 'method not allowed' })
      return
    }
    try {
      await settings.update(await readPatch(req))
      jsonResponse(res, 200, settings.get())
    } catch (error) {
      jsonResponse(res, 400, { error: error instanceof Error ? error.message : String(error) })
    }
  }
}

/**
 * Open one action-loop folder in the system file manager.
 *
 * This deliberately does NOT use the client `workspaces.openPath` funnel: the
 * better-sidebar plugin replaces that method and reroutes opens into its own
 * sidebar editor, which cannot display directories. Instead the host resolves
 * the manifest-declared folder name against `assets/pet` and hands it straight
 * to the OS file manager (`explorer.exe` on Windows, `open` on macOS), so
 * the user always lands in the OS file manager.
 */
export function createOpenFolderHandler() {
  return async (req, res) => {
    if (!isLoopback(req.socket?.remoteAddress)) {
      jsonResponse(res, 403, { error: 'local access only' })
      return
    }
    const origin = req.headers?.origin
    if (origin) {
      let originHost
      try { originHost = new URL(origin).host } catch {}
      if (!originHost || originHost !== req.headers.host) {
        jsonResponse(res, 403, { error: 'origin mismatch' })
        return
      }
    }
    if (req.method !== 'GET') {
      jsonResponse(res, 405, { error: 'method not allowed' })
      return
    }
    const url = new URL(req.url ?? '/', 'http://x')
    const folder = url.searchParams.get('folder') ?? ''
    let target
    if (folder === '' || folder === '.') {
      target = PET_ASSET_DIR
    } else if (/^[^/\\:\0]+$/.test(folder)) {
      target = resolve(PET_ASSET_DIR, folder)
      if (target !== PET_ASSET_DIR && !target.startsWith(`${PET_ASSET_DIR}${sep}`)) {
        jsonResponse(res, 400, { error: 'folder escapes the pet asset root' })
        return
      }
    } else {
      jsonResponse(res, 400, { error: 'invalid folder' })
      return
    }
    try {
      const info = await stat(target)
      if (!info.isDirectory()) {
        jsonResponse(res, 400, { error: 'folder is not a directory' })
        return
      }
    } catch {
      jsonResponse(res, 404, { error: 'folder not found' })
      return
    }
    try {
      await new Promise((resolveSpawn, rejectSpawn) => {
        const command = process.platform === 'darwin' ? 'open' : 'explorer.exe'
        const child = spawn(command, [target], { detached: true, stdio: 'ignore' })
        child.once('error', rejectSpawn)
        child.once('spawn', resolveSpawn)
        child.unref()
      })
      jsonResponse(res, 200, { opened: true })
    } catch (error) {
      jsonResponse(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  }
}

const ASSET_MIME = Object.freeze({
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
})

function createAssetHandler() {
  return async (req, res) => {
    const pathname = new URL(req.url ?? '/', 'http://x').pathname
    if (!pathname.startsWith(`${ASSET_PREFIX}/`)) {
      res.writeHead(404)
      res.end()
      return
    }
    let relative
    try {
      relative = decodeURIComponent(pathname.slice(ASSET_PREFIX.length + 1))
    } catch {
      res.writeHead(400)
      res.end()
      return
    }
    const target = resolve(assetRoot, normalize(relative))
    if (target !== assetRoot && !target.startsWith(`${assetRoot}${sep}`)) {
      res.writeHead(403)
      res.end()
      return
    }
    try {
      const data = await readFile(target)
      const extension = target.slice(target.lastIndexOf('.')).toLowerCase()
      res.writeHead(200, {
        'content-type': ASSET_MIME[extension] ?? 'application/octet-stream',
        'cache-control': 'no-store',
      })
      res.end(data)
    } catch {
      res.writeHead(404)
      res.end()
    }
  }
}

function mount(ctx, config = {}, eventCtx = ctx) {
  const logger = ctx.logger ?? console
  const base = publicConfig(config)
  const settings = ctx.settings?.register?.('dsh-dafeiyu', Config, {
    base,
    applies: 'live',
  }) ?? localSettingsScope(base)

  let bridge
  let reducer

  const readThemePreference = () => {
    const section = ctx.settings?.get?.('ui-theme')
    const preference = section?.preference
    return preference === 'light' || preference === 'dark' || preference === 'system'
      ? preference
      : 'system'
  }

  const sendTheme = () => {
    if (!bridge) return
    bridge.send(createMessage(CompanionMessageKind.THEME, {
      preference: readThemePreference(),
    }))
  }

  const stopRuntime = (reason = 'settings-change') => {
    bridge?.stop(reason)
    bridge = undefined
    reducer = undefined
  }

  const startRuntime = (resolved) => {
    if (resolved.enabled === false) {
      logger.info?.('dsh-dafeiyu is disabled')
      return
    }
    const helperConfig = config.helper ?? {}
    bridge = new HelperProcess({
      ...helperConfig,
      env: {
        ...helperConfig.env,
        DSH_DAFEIYU_SCALE: String(resolved.scale ?? defaults.scale),
        DSH_DAFEIYU_ACTIVITY_LEVEL: String(resolved.activityLevel ?? defaults.activityLevel),
        DSH_DAFEIYU_REDUCED_MOTION: resolved.reducedMotion === true ? '1' : '0',
      },
    }, logger)
    reducer = new CompanionReducer({ includeSubagents: resolved.includeSubagents === true })
    bridge.start()
    bridge.send(createMessage(CompanionMessageKind.HELLO, {
      state: CompanionState.IDLE,
      host: 'deepseek-harness',
      pluginVersion: '0.1.0-alpha.6',
      message: 'BigFish connected to DSH',
    }))
    bridge.send(createMessage(CompanionMessageKind.STATE, {
      state: CompanionState.IDLE,
      phase: 'plugin-start',
      stage: '等待任务',
      message: '我在这儿等新任务哦',
      detail: 'DSH · 等待下一次任务',
    }))
    sendTheme()
    logger.info?.('dsh-dafeiyu companion bridge started')
  }

  startRuntime(settings.get())

  // The companion intentionally observes every DSH session. Loader entries may
  // live inside a scoped composition, so use the unscoped root bus and dispose
  // the registrations explicitly with this plugin's lifecycle.
  const offEvent = eventCtx.on('session/event', (session, event) => {
    if (!bridge || !reducer) return
    for (const message of reducer.handle(session, event)) bridge.send(message)
  }, { global: true })
  const offDisposed = eventCtx.on('session/disposed', (session) => {
    if (!bridge || !reducer) return
    for (const message of reducer.disposeSession(session)) bridge.send(message)
  }, { global: true })
  const offTheme = eventCtx.on('settings/updated', (ns) => {
    if (ns === 'ui-theme' && bridge) sendTheme()
  }, { global: true })

  let lastRuntimeSettings = settings.get()
  const unwatch = settings.watch((next) => {
    const enabledChanged = (next.enabled ?? defaults.enabled) !== (lastRuntimeSettings.enabled ?? defaults.enabled)
    const subagentsChanged = (next.includeSubagents === true) !== (lastRuntimeSettings.includeSubagents === true)
    lastRuntimeSettings = next
    if (enabledChanged || subagentsChanged || !bridge) {
      // 启用/子 Agent 开关需要重建运行时；其余设置实时下发，避免每次调整
      // 都重启 helper（旧窗口还要播 3.4s 离场，快速拖动会叠加出多个窗口）。
      stopRuntime()
      startRuntime(next)
      return
    }
    bridge?.send(createMessage(CompanionMessageKind.SETTINGS, {
      scale: next.scale ?? defaults.scale,
      activityLevel: next.activityLevel ?? defaults.activityLevel,
      reducedMotion: next.reducedMotion === true,
    }))
  })
  if (typeof ctx.inject === 'function') {
    ctx.inject(['webServer'], (httpCtx) => {
      httpCtx.effect(
        () => httpCtx.webServer.register({ kind: 'exact', path: CONFIG_ENDPOINT, handler: createConfigHandler(settings) }),
        'dsh-dafeiyu: local settings endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({ kind: 'exact', path: OPEN_FOLDER_ENDPOINT, handler: createOpenFolderHandler() }),
        'dsh-dafeiyu: open pet folder in OS file manager',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({ kind: 'prefix', path: ASSET_PREFIX, handler: createAssetHandler() }),
        'dsh-dafeiyu: static pet assets',
      )
    })
  }
  ctx.effect(() => () => {
    offEvent?.()
    offDisposed?.()
    offTheme?.()
    unwatch()
    stopRuntime('dsh-host-stop')
  })
}

export function apply(ctx, config = {}) {
  if (typeof ctx.inject === 'function') {
    ctx.inject(['settings'], (settingsCtx) => mount(settingsCtx, config, ctx))
    return
  }
  mount(ctx, config)
}

export {
  CompanionMessageKind,
  CompanionReducer,
  CompanionState,
  HelperProcess,
}
