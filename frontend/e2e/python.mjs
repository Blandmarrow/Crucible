// The Python the e2e servers run on, shared by serve.mjs and serve-comfy.mjs.
//
// Works both locally (the repo venv) and in CI (setup-python + pip, no venv, so
// `python` on PATH). The venv is put first on PATH too, as `activate` would, so
// anything a server spawns by name resolves inside it.
import * as fs from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

/** Resolve the interpreter, editing `env` in place for the venv case. */
export function findPython(env) {
  const isWin = process.platform === 'win32'
  const venvBin = path.join(ROOT, 'venv', isWin ? 'Scripts' : 'bin')
  const venvPython = path.join(venvBin, isWin ? 'python.exe' : 'python')
  if (!fs.existsSync(venvPython)) return 'python'
  env.VIRTUAL_ENV = path.join(ROOT, 'venv')
  // Windows env keys are case-insensitive but a spread object's are not: reuse
  // whichever spelling of PATH is already there rather than adding a second.
  const pathKey = Object.keys(env).find((k) => k.toUpperCase() === 'PATH') ?? 'PATH'
  env[pathKey] = [venvBin, env[pathKey]].filter(Boolean).join(path.delimiter)
  return venvPython
}

/** Forward SIGINT/SIGTERM to `child`, and exit the way it exits.
 *
 * Playwright stops a webServer by killing its whole process tree (a process-
 * group SIGKILL on POSIX, `taskkill /T` on Windows), which reaches the child
 * directly. A signal sent to the launcher alone — `kill <pid>`, or Ctrl-C from a
 * manual run — is forwarded, so the child never outlives its launcher. */
export function tieLifetime(child, name) {
  child.on('error', (err) => {
    console.error(`could not start ${name}: ${err.message}`)
    process.exit(1)
  })
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      if (child.exitCode === null && child.signalCode === null) child.kill(sig)
    })
  }
  child.on('exit', (code, signal) => {
    if (signal) {
      // Die of the same signal, so the parent sees how the server ended.
      process.removeAllListeners(signal)
      process.kill(process.pid, signal)
      return
    }
    process.exit(code ?? 1)
  })
}
