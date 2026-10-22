#!/usr/bin/env node
// PreToolUse guard for Autona.
//
// Enforces rules that prose in CLAUDE.md / .claude/orchestration.md has
// failed to prevent:
//   R1 push-to-main  remote main must only ever be updated through a pull request
//   R2 app-launch    launching the app (main.py / Autona.exe) is the owner's job
//   R3 exe-build     PyInstaller builds only on explicit instruction
//
// Fail-open by design: a guard that cannot parse its input must never block work.

function decide(permissionDecision, reason) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision,
        permissionDecisionReason: reason,
      },
    })
  );
  process.exit(0);
}

const deny = (reason) => decide('deny', reason);
const ask = (reason) => decide('ask', reason);

// Heredoc bodies are data, not commands: a commit message that quotes `git push`
// must not be read as an invocation of it. The body is dropped while the
// commands around it are kept.
function stripHeredocs(command) {
  const lines = command.split(/\r?\n/);
  const out = [];
  let terminator = null;

  for (const line of lines) {
    if (terminator === null) {
      out.push(line);
      const m = line.match(/<<-?\s*(['"]?)(\w+)\1/);
      if (m) terminator = m[2];
    } else if (line.trim() === terminator) {
      terminator = null;
    }
  }
  return out.join('\n');
}

// Split a command line into statements at ; && || | & and newlines, outside quotes.
// `2>&1` / `&>` are redirections, not separators.
function splitStatements(text) {
  const out = [];
  let cur = '';
  let quote = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      if (ch === quote) quote = null;
      cur += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      cur += ch;
      continue;
    }
    const isSep =
      ch === ';' ||
      ch === '\n' ||
      ch === '|' ||
      (ch === '&' && text[i - 1] !== '>' && text[i + 1] !== '>');
    if (isSep) {
      out.push(cur);
      cur = '';
      // swallow the second char of && and ||
      if ((ch === '&' || ch === '|') && text[i + 1] === ch) i++;
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out.filter((x) => x.trim());
}

function segments(command) {
  // Line continuations (backslash + newline) join into one statement.
  const joined = stripHeredocs(command).replace(/\\\r?\n/g, ' ').replace(/\r/g, '');
  return splitStatements(joined);
}

// One shell word: runs of plain chars and quoted spans, so a value such as
// `credential.helper="!gh auth git-credential"` counts as a single word.
const WORD = String.raw`(?:[^\s"']|"[^"]*"|'[^']*')+`;
const words = (s) => s.match(new RegExp(WORD, 'g')) || [];
const unquote = (w) => w.replace(/["']/g, '');
const basename = (w) => unquote(w).split(/[\\/]/).pop();

// Leading wrappers that do not change which program runs.
const WRAPPERS = [
  /^&\s*/, // PowerShell call operator
  new RegExp(String.raw`^[A-Za-z_]\w*=(?:[^\s"']|"[^"]*"|'[^']*')*\s+`), // FOO=1
  /^env\s+(?:-\S+\s+)*/i,
  /^sudo\s+(?:-\S+\s+)*/i,
  /^timeout\s+(?:-\S+\s+)*\d+[smhd]?\s+/i,
  /^(?:nohup|exec|command)\s+/i,
  /^start-process\s+(?:-(?:wait|nonewwindow|passthru)\s+)*(?:-filepath\s+)?/i,
  /^cmd(?:\.exe)?\s+\/[ck]\s+/i,
  /^start\s+(?:\/\w+\s+)*(?:"[^"]*"\s+(?=\S))?/i,
];
const SHELL_C = /^("[^"]*"|'[^']*'|\S+)\s+((?:-\S+\s+)*?)-(?:c|command)\s+([\s\S]+)$/i;
const SHELLS = new Set(['powershell', 'pwsh', 'bash', 'sh', 'zsh']);

// Strip wrappers and return the effective statements (a quoted
// `powershell -c "..."` body expands into several).
function expand(statement, depth = 0) {
  let s = statement.trim();
  for (let i = 0; i < 20 && depth < 5; i++) {
    s = s.replace(/^[\s(]+/, '');
    const sh = s.match(SHELL_C);
    if (sh && SHELLS.has(basename(sh[1]).toLowerCase().replace(/\.exe$/, ''))) {
      let inner = sh[3].trim();
      const q = inner.match(/^(["'])([\s\S]*)\1$/);
      if (q) inner = q[2];
      return segments(inner).flatMap((x) => expand(x, depth + 1));
    }
    const re = WRAPPERS.find((r) => r.test(s));
    if (!re) break;
    s = s.replace(re, '');
  }
  return [s.replace(/[)\s]+$/, '')];
}

// Python launcher words -> { module, script } (first non-flag argument).
function pythonInfo(ws) {
  if (!ws.length || !/^(?:python[\d.]*|py)(?:\.exe)?$/i.test(basename(ws[0]))) return null;
  for (let i = 1; i < ws.length; i++) {
    const w = unquote(ws[i]);
    if (w === '-m') return { module: unquote(ws[i + 1] || '') };
    if (w === '-c') return {};
    if (w === '-X' || w === '-W') i++; // value-taking flags
    else if (!w.startsWith('-')) return { script: basename(w) };
  }
  return {};
}

// R1: any push whose destination ref is main (or HEAD / @, which resolve to the
// local main branch in this workflow), plus the bare form, which resolves to the
// upstream of the local main branch and so lands on remote main too.
function checkGitPush(ws) {
  if (!ws.length || !/^git(?:\.exe)?$/i.test(basename(ws[0]))) return null;
  let i = 1;
  // Skip global options: -C/-c/--git-dir/--work-tree/... take a value, the rest are flags.
  while (i < ws.length && ws[i].startsWith('-')) {
    const w = unquote(ws[i]);
    i += /^(?:-C|-c|--git-dir|--work-tree|--namespace|--exec-path|--super-prefix)$/.test(w) ? 2 : 1;
  }
  if (unquote(ws[i] || '').toLowerCase() !== 'push') return null;

  const args = ws.slice(i + 1).filter((w) => !/^\d*[<>]/.test(w));
  const flags = args.filter((a) => a.startsWith('-')).map(unquote);
  const del = flags.some((f) => f === '--delete' || f === '-d');
  const positional = args.filter((a) => !a.startsWith('-')).map(unquote);
  const refspecs = positional.slice(1); // positional[0] is the remote

  if (refspecs.length === 0) {
    if (del) return null;
    // `git push --tags [origin]` only sends tags; every other bare form hits main.
    if (flags.includes('--tags') && !flags.some((f) => f === '--all' || f === '--mirror')) return null;
    return 'bare';
  }

  for (const spec of refspecs) {
    const dst = spec.includes(':') ? spec.slice(spec.indexOf(':') + 1) : spec;
    const name = dst.replace(/^\+/, '').replace(/^refs\/heads\//, '');
    if (name === 'main') return 'main';
    if (!del && (name === 'HEAD' || name === '@')) return 'main';
  }
  return null;
}

// R2: python main.py (py / python3 / path-qualified, with interpreter flags) or Autona.exe.
function isAppLaunch(ws) {
  if (!ws.length) return false;
  if (basename(ws[0]).toLowerCase() === 'autona.exe') return true;
  return pythonInfo(ws)?.script?.toLowerCase() === 'main.py';
}

// R3: PyInstaller invoked directly or through `python -m PyInstaller`.
function isExeBuild(ws) {
  if (!ws.length) return false;
  if (/^pyinstaller(?:\.exe)?$/i.test(basename(ws[0]))) return true;
  return pythonInfo(ws)?.module?.toLowerCase() === 'pyinstaller';
}

let raw = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) raw += chunk;

let input;
try {
  input = JSON.parse(raw);
} catch {
  process.exit(0);
}

const command = input?.tool_input?.command;
if (typeof command !== 'string' || !command.trim()) process.exit(0);

for (const statement of segments(command)) {
  for (const stmt of expand(statement)) {
    const ws = words(stmt);

    const push = checkGitPush(ws);
    if (push === 'main') {
      deny(
        'リモート main への直接 push は禁止です（CLAUDE.md / .claude/git-workflow.md）。' +
          '必ず PR 経由でマージしてください。push は `git push origin main:<type>/<topic>`' +
          '（種別 feat / fix / docs / chore / ci）の形にしてください。'
      );
    }
    if (push === 'bare') {
      deny(
        '引数なしの `git push` はローカル main の upstream（origin/main）へ push されるため禁止です。' +
          '`git push origin main:<type>/<topic>` の形で明示してください。'
      );
    }

    if (isAppLaunch(ws)) {
      ask(
        '実機の起動・GUI 操作はオーナーが行う運用です（.claude/orchestration.md）。' +
          '明示指示がある場合のみ許可してください。'
      );
    }

    if (isExeBuild(ws)) {
      ask('exe ビルドは明示指示があるときだけ実行します（CLAUDE.md ビルド節）。');
    }
  }
}

process.exit(0);
