'use strict';

/*
 * Ingest orchestrator.
 *
 * Pipeline (each step is one of the existing Python scripts, run as a child
 * process; progress is scraped from its stdout):
 *
 *   download        download_frt.py  → /data/pdf/frt-XXXX.pdf
 *   parse ┐         parse_frt.py     → work/frt_output.json      (run concurrently)
 *   split ┘         split_frn.py     → work/frt_pages/*.pdf
 *   schema          createDb.py      → empty <FRT>_staging database
 *   import_records  importIntoDb.py  → records into staging
 *   import_pdfs       (same process)  → per-FRN PDFs into staging
 *   promote         swap_db.py       → validate, then atomic RENAME staging → live
 *   cleanup                           → remove work files and superseded PDFs
 *
 * The live database is only touched by the single atomic RENAME in `promote`,
 * so it stays online and consistent for the whole run, and a failed or
 * cancelled run leaves it exactly as it was.
 */

const fs = require('fs');
const fsp = fs.promises;
const path = require('path');
const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const config = require('./config');
const db = require('./db');

const STAGE_DEFS = [
  { id: 'download', label: 'Download latest FRT PDF', weight: 4 },
  { id: 'parse', label: 'Parse PDF into records', weight: 50 },
  { id: 'split', label: 'Split PDF into per-FRN PDFs', weight: 10 },
  { id: 'schema', label: 'Create staging database', weight: 1 },
  { id: 'import_records', label: 'Import records into staging', weight: 15 },
  { id: 'import_pdfs', label: 'Import PDFs into staging', weight: 15 },
  { id: 'promote', label: 'Validate and swap into live', weight: 4 },
  { id: 'cleanup', label: 'Clean up', weight: 1 },
];

const LOG_LIMIT = 1500;
const HISTORY_LIMIT = 30;
const PDF_DIR = path.join(config.dataDir, 'pdf');
const WORK_ROOT = path.join(config.dataDir, 'work');
const LOG_DIR = path.join(config.dataDir, 'logs');
const STATE_FILE = path.join(config.dataDir, 'state.json');

const nowIso = () => new Date().toISOString();
const num = (s) => Number(String(s).replace(/,/g, ''));
const fmt = (n) => Number(n).toLocaleString('en-US');

class CancelledError extends Error {
  constructor() {
    super('Cancelled by user');
    this.cancelled = true;
  }
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Split a child's output into segments on \r or \n. `complete` is true for \n-terminated lines. */
function splitStream(stream, onSegment) {
  let buf = '';
  stream.setEncoding('utf8');
  stream.on('data', (chunk) => {
    buf += chunk;
    let start = 0;
    for (let i = 0; i < buf.length; i++) {
      const c = buf[i];
      if (c === '\n' || c === '\r') {
        onSegment(buf.slice(start, i), c === '\n');
        start = i + 1;
      }
    }
    buf = buf.slice(start);
  });
  stream.on('end', () => {
    if (buf) onSegment(buf, true);
    buf = '';
  });
}

function recordSummary(rows) {
  if (!rows.dbo_frn) return undefined;
  return `${rows.dbo_frn} FRNs · ${rows.dbo_frnSubEntry || 0} sub-entries · ${rows.dbo_frnNote || 0} notes · ${rows.dbo_frnCrossReference || 0} cross-refs`;
}

function dbArgs(database) {
  return [
    '--host', config.db.host,
    '--port', String(config.db.port),
    '--database', database,
    '--user', config.db.user,
    '--password', config.db.password,
  ];
}

function swapArgs(command) {
  return [
    'swap_db.py', command,
    '--host', config.db.host,
    '--port', String(config.db.port),
    '--user', config.db.user,
    '--live', config.liveDb,
    '--staging', config.stagingDb,
    '--previous', config.previousDb,
  ];
}

function childEnv() {
  // Password reaches swap_db.py via env; drop the UI credentials from children.
  const env = { ...process.env, PYTHONUNBUFFERED: '1', FRT_SCRIPTS_DIR: config.scriptsDir };
  delete env.UI_PASSWORD;
  return env;
}

class Ingest extends EventEmitter {
  constructor() {
    super();
    this.state = { current: null, previous: null, history: [], activeJob: null };
    this.job = null;
    this.log = [];
    this.children = new Set();
    this.busy = null; // 'ingest' | 'rollback' | null
    this.logStream = null;
  }

  // ------------------------------------------------------------------ state

  async init() {
    for (const dir of [PDF_DIR, WORK_ROOT, LOG_DIR]) await fsp.mkdir(dir, { recursive: true });
    try {
      this.state = { ...this.state, ...JSON.parse(await fsp.readFile(STATE_FILE, 'utf8')) };
    } catch (err) {
      if (err.code !== 'ENOENT') console.error('Could not read state file:', err.message);
    }

    // Nothing can be running at startup: clear leftovers of an interrupted run.
    await fsp.rm(WORK_ROOT, { recursive: true, force: true });
    await fsp.mkdir(WORK_ROOT, { recursive: true });

    const interrupted = this.state.activeJob;
    if (interrupted) {
      console.log(`Previous ingest ${interrupted.id} was interrupted; cleaning up.`);
      if (interrupted.downloadingPdf) await fsp.rm(interrupted.downloadingPdf, { force: true });
      await db.dropDatabase(config.stagingDb).catch((e) => console.error(e.message));
      await this.exec(swapArgs('repair')).catch((e) => console.error('repair failed:', e.message));
      this.addHistory({
        ...interrupted,
        downloadingPdf: undefined,
        status: 'interrupted',
        finishedAt: nowIso(),
        error: 'The container stopped while this ingest was running. The live database was not changed.',
      });
      this.state.activeJob = null;
      await this.saveState();
    }
  }

  async saveState() {
    const tmp = `${STATE_FILE}.tmp`;
    await fsp.writeFile(tmp, JSON.stringify(this.state, null, 2));
    await fsp.rename(tmp, STATE_FILE);
  }

  addHistory(entry) {
    this.state.history.unshift(entry);
    this.state.history.length = Math.min(this.state.history.length, HISTORY_LIMIT);
  }

  snapshot() {
    return {
      busy: this.busy,
      job: this.job ? { ...this.job, progress: this.overallProgress() } : null,
      current: this.state.current,
      previous: this.state.previous,
      history: this.state.history,
      config: {
        liveDb: config.liveDb,
        stagingDb: config.stagingDb,
        previousDb: config.previousDb,
        parseWorkers: config.parseWorkers,
        keepPrevious: config.keepPrevious,
        minRowRatio: config.minRowRatio,
      },
    };
  }

  recentLog() {
    return this.log;
  }

  overallProgress() {
    if (!this.job) return 0;
    let total = 0;
    let done = 0;
    for (const s of this.job.stages) {
      total += s.weight;
      if (s.status === 'done' || s.status === 'skipped') done += s.weight;
      else if (s.status === 'running') done += s.weight * (s.progress || 0);
    }
    return total ? done / total : 0;
  }

  changed() {
    this.emit('change');
  }

  // ---------------------------------------------------------------- logging

  logLine(text, stage = 'job') {
    const line = { t: Date.now(), stage, text };
    this.log.push(line);
    if (this.log.length > LOG_LIMIT) this.log.splice(0, this.log.length - LOG_LIMIT);
    if (this.logStream) this.logStream.write(`${new Date(line.t).toISOString()} [${stage}] ${text}\n`);
    this.emit('log', line);
  }

  // ----------------------------------------------------------------- stages

  stage(id) {
    return this.job.stages.find((s) => s.id === id);
  }

  startStage(id, detail = '') {
    const s = this.stage(id);
    Object.assign(s, { status: 'running', startedAt: nowIso(), progress: null, detail });
    this.logLine(`▶ ${s.label}`, id);
    this.changed();
  }

  updateStage(id, patch) {
    Object.assign(this.stage(id), patch);
    this.changed();
  }

  finishStage(id, detail) {
    const s = this.stage(id);
    Object.assign(s, { status: 'done', progress: 1, finishedAt: nowIso() });
    if (detail !== undefined) s.detail = detail;
    this.logLine(`✔ ${s.label}${detail ? ` — ${detail}` : ''}`, id);
    this.changed();
  }

  // -------------------------------------------------------------- processes

  /**
   * Run a Python script from the FRT scripts dir. Every \n-terminated line is
   * logged under `stage` (a string or a function returning one); every
   * segment, including \r progress updates, is passed to `onSegment`.
   */
  runPython(stage, args, onSegment) {
    return new Promise((resolve, reject) => {
      if (this.job && this.job.cancelRequested) return reject(new CancelledError());
      const child = spawn(config.python, args, {
        cwd: config.scriptsDir,
        env: childEnv(),
        detached: true, // own process group, so cancel also stops multiprocessing workers
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      this.children.add(child);
      const handle = (seg, complete) => {
        const text = seg.trim();
        if (!text) return;
        if (complete) this.logLine(seg.trimEnd(), typeof stage === 'function' ? stage() : stage);
        if (onSegment) {
          try {
            onSegment(text, complete);
          } catch (err) {
            console.error('progress parser error:', err);
          }
        }
      };
      splitStream(child.stdout, handle);
      splitStream(child.stderr, handle);
      child.on('error', (err) => {
        this.children.delete(child);
        reject(err);
      });
      child.on('close', (code, signal) => {
        this.children.delete(child);
        if (this.job && this.job.cancelRequested) return reject(new CancelledError());
        if (code === 0) return resolve();
        const err = new Error(`${args[0]} exited with ${code !== null ? `code ${code}` : signal}`);
        err.exitCode = code;
        reject(err);
      });
    });
  }

  /** Run a Python script outside a job; output goes to the console and is returned. */
  exec(args) {
    return new Promise((resolve, reject) => {
      const child = spawn(config.python, args, { cwd: config.scriptsDir, env: childEnv() });
      const lines = [];
      const handle = (seg, complete) => {
        if (complete && seg.trim()) {
          lines.push(seg.trimEnd());
          console.log(`[${args[0]}] ${seg.trimEnd()}`);
        }
      };
      splitStream(child.stdout, handle);
      splitStream(child.stderr, handle);
      child.on('error', reject);
      child.on('close', (code) => {
        if (code === 0) return resolve(lines);
        const msg = [...lines].reverse().find((l) => /failed|error/i.test(l)) || `exit code ${code}`;
        const err = new Error(msg);
        err.lines = lines;
        reject(err);
      });
    });
  }

  killChildren() {
    for (const child of this.children) {
      try {
        process.kill(-child.pid, 'SIGTERM');
      } catch {
        /* already gone */
      }
      setTimeout(() => {
        try {
          process.kill(-child.pid, 'SIGKILL');
        } catch {
          /* already gone */
        }
      }, 10000).unref();
    }
  }

  /** Like Promise.all, but the first failure stops the sibling processes. */
  async allOrAbort(promises) {
    let first = null;
    await Promise.all(
      promises.map((p) =>
        p.catch((err) => {
          if (!first) {
            first = err;
            this.killChildren();
          }
        })
      )
    );
    if (first) throw first;
  }

  // -------------------------------------------------------------- public API

  start({ force = false } = {}) {
    if (this.busy) throw new HttpError(409, `Busy: a ${this.busy} is already running`);
    this.busy = 'ingest';
    const started = new Date();
    const id = started.toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
    this.job = {
      id,
      status: 'running',
      force: Boolean(force),
      startedAt: started.toISOString(),
      finishedAt: null,
      pdf: null,
      error: null,
      warnings: [],
      counts: null,
      canCancel: true,
      cancelRequested: false,
      stages: STAGE_DEFS.map((s) => ({
        ...s,
        status: 'pending',
        progress: null,
        detail: '',
        startedAt: null,
        finishedAt: null,
      })),
    };
    this.log = [];
    this.logStream = fs.createWriteStream(path.join(LOG_DIR, `${id}.log`), { flags: 'a' });
    this.state.activeJob = { id, startedAt: this.job.startedAt, force: this.job.force };
    this.changed();
    this.run().catch((err) => console.error('ingest crashed:', err));
    return this.job;
  }

  cancel() {
    if (!this.job || this.job.status !== 'running') throw new HttpError(409, 'No ingest is running');
    if (!this.job.canCancel) {
      throw new HttpError(409, 'The swap into the live database is in progress and cannot be interrupted');
    }
    this.job.cancelRequested = true;
    this.logLine('Cancel requested — stopping…');
    this.killChildren();
    this.changed();
  }

  async rollback() {
    if (this.busy) throw new HttpError(409, `Busy: a ${this.busy} is already running`);
    this.busy = 'rollback';
    this.changed();
    const startedAt = nowIso();
    try {
      await this.exec([...swapArgs('rollback'), '--keep-previous']);
      const { current, previous } = this.state;
      this.state.current = previous || { pdf: null, ingestedAt: null };
      this.state.previous = current || { pdf: null, ingestedAt: null };
      this.addHistory({
        id: `rollback-${Date.now()}`,
        type: 'rollback',
        status: 'rolled_back',
        pdf: this.state.current.pdf,
        startedAt,
        finishedAt: nowIso(),
      });
      await this.saveState();
    } catch (err) {
      throw new HttpError(500, `Rollback failed: ${err.message}`);
    } finally {
      this.busy = null;
      this.changed();
    }
  }

  // ---------------------------------------------------------------- pipeline

  async run() {
    const job = this.job;
    const workDir = path.join(WORK_ROOT, job.id);
    const jsonPath = path.join(workDir, 'frt_output.json');
    const pagesDir = path.join(workDir, 'frt_pages');
    let pdfPath = null;
    let downloading = false;
    let freshDownload = false;
    let stagingCreated = false;
    let keepStaging = false;
    let swapped = false;

    try {
      await this.saveState();
      await fsp.mkdir(pagesDir, { recursive: true });
      this.logLine(`Ingest ${job.id} started${job.force ? ' (forced re-ingest)' : ''}`);

      // ---- download -------------------------------------------------------
      this.startStage('download', 'Checking rcmp.ca for the latest edition…');
      await this.runPython('download', ['download_frt.py', PDF_DIR], (seg) => {
        let m;
        if ((m = seg.match(/^Downloading (\S+) → (.+)$/))) {
          pdfPath = m[2].trim();
          downloading = true;
          freshDownload = true;
          this.state.activeJob.downloadingPdf = pdfPath;
          this.saveState().catch(() => {});
          this.updateStage('download', { detail: `Downloading ${path.basename(pdfPath)}…` });
        } else if ((m = seg.match(/^(.+?) already exists — skipping download\.$/))) {
          pdfPath = m[1].trim();
        } else if ((m = seg.match(/(\d+) MB \/ (\d+) MB \((\d+)%\)/))) {
          this.updateStage('download', {
            progress: num(m[3]) / 100,
            detail: `${path.basename(pdfPath || '')} · ${fmt(m[1])} / ${fmt(m[2])} MB`,
          });
        }
      });
      downloading = false;
      delete this.state.activeJob.downloadingPdf;
      if (!pdfPath || !fs.existsSync(pdfPath)) throw new Error('Could not determine the downloaded PDF path');
      job.pdf = path.basename(pdfPath);
      const sizeMb = Math.round((await fsp.stat(pdfPath)).size / 2 ** 20);
      this.finishStage(
        'download',
        `${job.pdf} (${fmt(sizeMb)} MB)${freshDownload ? '' : ' — already downloaded'}`
      );

      if (!job.force && this.state.current && this.state.current.pdf === job.pdf) {
        for (const s of job.stages) if (s.status === 'pending') s.status = 'skipped';
        job.status = 'up_to_date';
        this.logLine(`${job.pdf} is already the live edition — nothing to do. Use "re-ingest" to force it.`);
        return;
      }

      // ---- parse + split (concurrently) ----------------------------------
      let recordCount = 0;
      let parseError = null;
      const parallel = config.parseWorkers !== 1;
      this.startStage('parse', 'Opening PDF…');
      this.startStage('split', 'Opening PDF…');

      const parseP = this.runPython(
        'parse',
        ['parse_frt.py', pdfPath, jsonPath, '-w', String(config.parseWorkers)],
        (seg) => {
          let m;
          if ((m = seg.match(/^([\d,]+)\/([\d,]+)\s+\(\s*([\d.]+)%\)\s+records: ([\d,]+)\s+([\d,]+) pg\/s\s+ETA (\S+)/))) {
            this.updateStage('parse', {
              progress: num(m[3]) / 100,
              detail: `Page ${m[1]} of ${m[2]} · ${m[4]} records · ${m[5]} pg/s · ETA ${m[6]}`,
            });
          } else if ((m = seg.match(/^FRNs parsed: ([\d,]+)\s+\(~?([\d.]+)%\)(?:\s+([\d,]+) FRN\/s)?/))) {
            // Parallel mode counts FRN pages, not all pages, so this is an estimate.
            this.updateStage('parse', {
              progress: Math.min(num(m[2]) / 100, 0.99),
              detail: `${m[1]} FRN pages parsed (~${m[2]}%)${m[3] ? ` · ${m[3]} FRN/s` : ''}`,
            });
          } else if (/^Assembling records/.test(seg)) {
            this.updateStage('parse', { detail: seg });
          } else if (/^Writing output/.test(seg)) {
            this.updateStage('parse', { detail: 'Writing JSON…' });
          } else if ((m = seg.match(/^Records extracted:\s+([\d,]+)/))) {
            recordCount = num(m[1]);
          } else if ((m = seg.match(/^Error during (?:parallel )?parsing: (.*)$/))) {
            parseError = m[1];
          } else if ((m = seg.match(/^(Worker \d+ error .*)$/))) {
            parseError = m[1]; // a failed worker silently drops its pages — treat as fatal
          }
        }
      ).then(() => {
        // parse_frt.py exits 0 even when it fails, so check what it reported.
        if (parseError) throw new Error(`parse_frt.py: ${parseError}`);
        if (!recordCount) throw new Error('parse_frt.py produced no records');
        this.finishStage('parse', `${fmt(recordCount)} records extracted${parallel ? ` (${config.parseWorkers || 'auto'} workers)` : ''}`);
      });

      const splitP = this.runPython(
        'split',
        ['split_frn.py', pdfPath, pagesDir, '-w', String(config.splitWriters)],
        (seg) => {
          let m;
          if ((m = seg.match(/^([\d,]+)\/([\d,]+)\s+found: ([\d,]+)\s+written: ([\d,]+)\s+([\d,]+) pg\/s/))) {
            this.updateStage('split', {
              progress: num(m[1]) / num(m[2]),
              detail: `Page ${m[1]} of ${m[2]} · ${m[4]} PDFs written · ${m[5]} pg/s`,
            });
          } else if ((m = seg.match(/^(\d+) write error\(s\)/))) {
            job.warnings.push(`split_frn.py: ${m[1]} PDF write error(s)`);
          }
        }
      ).then(async () => {
        const count = (await fsp.readdir(pagesDir)).filter((f) => f.endsWith('.pdf')).length;
        if (!count) throw new Error('split_frn.py produced no PDFs');
        this.finishStage('split', `${fmt(count)} per-FRN PDFs written`);
      });

      await this.allOrAbort([parseP, splitP]);

      // ---- staging schema --------------------------------------------------
      this.startStage('schema', `Recreating ${config.stagingDb}…`);
      await db.recreateDatabase(config.stagingDb);
      stagingCreated = true;
      await this.runPython('schema', ['createDb.py', ...dbArgs(config.stagingDb)]);
      this.finishStage('schema', `${config.stagingDb} ready`);

      // ---- import (records, then PDFs, in one process) ---------------------
      let importStage = 'import_records';
      let pdfSkipped = 0;
      const imported = {};
      this.startStage('import_records', `0 of ${fmt(recordCount)} FRNs`);
      await this.runPython(
        () => importStage,
        ['importIntoDb.py', jsonPath, ...dbArgs(config.stagingDb), '--pages-dir', pagesDir],
        (seg) => {
          let m;
          if ((m = seg.match(/^([\d,]+) FRNs\s+([\d,]+) sub-entries\s+([\d,]+) notes\s+([\d,]+) cross-refs\s+\((\d+) rec\/s\)/))) {
            this.updateStage('import_records', {
              progress: Math.min(num(m[1]) / recordCount, 1),
              detail: `${m[1]} of ${fmt(recordCount)} FRNs · ${m[2]} sub-entries · ${m[3]} notes · ${m[5]} rec/s`,
            });
          } else if (/^Rebuilding fulltext/.test(seg)) {
            this.updateStage('import_records', { progress: 1, detail: 'Rebuilding fulltext indexes…' });
          } else if ((m = seg.match(/^fulltext (dbo_\w+): ([\d,]+) updated/))) {
            this.updateStage('import_records', { progress: 1, detail: `Rebuilding fulltext indexes… ${m[1]} done` });
          } else if ((m = seg.match(/^Source: .*\(([\d,]+) files\)$/))) {
            this.finishStage('import_records', recordSummary(imported));
            importStage = 'import_pdfs';
            this.startStage('import_pdfs', `0 of ${m[1]} PDFs`);
          } else if ((m = seg.match(/^([\d,]+)\/([\d,]+)\s+\((\d+) files\/s\)/))) {
            this.updateStage('import_pdfs', {
              progress: num(m[1]) / num(m[2]),
              detail: `${m[1]} of ${m[2]} PDFs · ${m[3]} files/s`,
            });
          } else if (/^\[skip\] FRN/.test(seg)) {
            pdfSkipped++;
          } else if ((m = seg.match(/^(dbo_\w+)\s+([\d,]+) rows/))) {
            imported[m[1]] = m[2]; // final summary lines
          }
        }
      );
      if (this.stage('import_records').status === 'running') this.finishStage('import_records', recordSummary(imported));
      if (this.stage('import_pdfs').status === 'running') {
        const loaded = imported.dbo_frnPdf ? `${imported.dbo_frnPdf} PDFs loaded` : 'PDFs loaded';
        this.finishStage('import_pdfs', pdfSkipped ? `${loaded} · ${fmt(pdfSkipped)} skipped (FRN not in records)` : loaded);
      } else if (this.stage('import_pdfs').status === 'pending') {
        this.stage('import_pdfs').status = 'skipped';
      }
      if (pdfSkipped) job.warnings.push(`${fmt(pdfSkipped)} PDFs had no matching FRN record and were skipped`);

      // ---- promote ------------------------------------------------------------
      job.canCancel = false;
      this.startStage('promote', 'Validating staging data…');
      let result = null;
      const args = [...swapArgs('promote'), '--min-ratio', String(config.minRowRatio)];
      if (config.keepPrevious) args.push('--keep-previous');
      try {
        await this.runPython('promote', args, (seg) => {
          if (seg.startsWith('RESULT ')) result = JSON.parse(seg.slice(7));
          else if (seg === 'SWAPPED') swapped = true;
          else if (!seg.startsWith('Validation failed')) this.updateStage('promote', { detail: seg });
        });
      } catch (err) {
        if (err.exitCode === 2) {
          keepStaging = true;
          const last = [...this.log].reverse().find((l) => l.text.startsWith('Validation failed'));
          throw new Error(last ? last.text : 'Staging validation failed');
        }
        throw err;
      }
      stagingCreated = false; // swap_db.py dropped it
      job.counts = result.live;
      this.recordSwap(job, result);
      this.finishStage('promote', `Live database now has ${fmt(result.live.dbo_frn)} FRNs`);

      // ---- cleanup ------------------------------------------------------------
      this.startStage('cleanup');
      await fsp.rm(workDir, { recursive: true, force: true });
      for (const f of await fsp.readdir(PDF_DIR)) {
        if (/^frt-.*\.pdf$/i.test(f) && f !== job.pdf) await fsp.rm(path.join(PDF_DIR, f), { force: true });
      }
      this.finishStage('cleanup');
      job.status = 'succeeded';
      this.logLine(`Ingest finished: ${job.pdf} is live.`);
    } catch (err) {
      const cancelled = Boolean(err.cancelled || job.cancelRequested);
      job.status = cancelled ? 'cancelled' : 'failed';
      job.error = cancelled ? 'Cancelled by user' : err.message;
      for (const s of job.stages) {
        if (s.status === 'running') Object.assign(s, { status: cancelled ? 'cancelled' : 'failed', finishedAt: nowIso() });
        else if (s.status === 'pending') s.status = 'skipped';
      }
      this.logLine(cancelled ? 'Ingest cancelled. The live database was not changed.' : `Ingest failed: ${err.message}`);
      if (swapped && !job.counts) {
        // The rename went through but a later step failed: live has the new data.
        this.recordSwap(job, null);
        this.logLine('Note: the swap into live completed before the failure; the new data is live.');
      } else if (!swapped) {
        this.logLine('The live database was not changed.');
      }
      if (downloading && pdfPath) await fsp.rm(pdfPath, { force: true }); // never keep a partial download
    } finally {
      if (job.status !== 'succeeded') await fsp.rm(workDir, { recursive: true, force: true }).catch(() => {});
      if (stagingCreated && !keepStaging) {
        await db.dropDatabase(config.stagingDb).catch((e) => this.logLine(`Could not drop staging: ${e.message}`));
      }
      if (keepStaging) this.logLine(`${config.stagingDb} was kept for inspection; the next ingest replaces it.`);
      job.finishedAt = nowIso();
      job.canCancel = false;
      this.addHistory({
        id: job.id,
        type: 'ingest',
        status: job.status,
        pdf: job.pdf,
        force: job.force,
        startedAt: job.startedAt,
        finishedAt: job.finishedAt,
        error: job.error,
        warnings: job.warnings,
        frns: job.counts ? job.counts.dbo_frn : null,
      });
      this.state.activeJob = null;
      await this.saveState().catch((e) => console.error('saveState failed:', e.message));
      if (this.logStream) this.logStream.end();
      this.logStream = null;
      this.busy = null;
      this.changed();
    }
  }

  recordSwap(job, result) {
    const hadLive = !result || Object.keys(result.previous || {}).length > 0;
    this.state.previous =
      config.keepPrevious && hadLive ? this.state.current || { pdf: null, ingestedAt: null } : null;
    this.state.current = {
      pdf: job.pdf,
      ingestedAt: nowIso(),
      jobId: job.id,
      counts: result ? result.live : null,
    };
  }
}

module.exports = { ingest: new Ingest(), HttpError, LOG_DIR };
