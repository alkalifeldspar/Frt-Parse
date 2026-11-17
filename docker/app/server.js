'use strict';

const path = require('path');
const crypto = require('crypto');
const express = require('express');
const config = require('./lib/config');
const db = require('./lib/db');
const { ingest, HttpError, LOG_DIR } = require('./lib/ingest');

const app = express();
app.disable('x-powered-by');

// --------------------------------------------------------------------- auth

const sha = (s) => crypto.createHash('sha256').update(String(s)).digest();
const expectedUser = sha(config.uiUser);
const expectedPass = sha(config.uiPassword);

function basicAuth(req, res, next) {
  const [scheme, encoded] = (req.headers.authorization || '').split(' ');
  if (scheme === 'Basic' && encoded) {
    const decoded = Buffer.from(encoded, 'base64').toString('utf8');
    const i = decoded.indexOf(':');
    if (i >= 0) {
      const userOk = crypto.timingSafeEqual(sha(decoded.slice(0, i)), expectedUser);
      const passOk = crypto.timingSafeEqual(sha(decoded.slice(i + 1)), expectedPass);
      if (userOk && passOk) return next();
    }
  }
  res.set('WWW-Authenticate', 'Basic realm="FRT Admin", charset="UTF-8"');
  res.status(401).send('Authentication required');
}

// Browsers attach cached Basic credentials to cross-site requests. Requiring a
// JSON body on state-changing routes forces a CORS preflight, which blocks CSRF.
function requireJson(req, res, next) {
  if (!req.is('application/json')) {
    return res.status(415).json({ error: 'Content-Type must be application/json' });
  }
  next();
}

const route = (fn) => (req, res, next) => Promise.resolve(fn(req, res)).catch(next);

// -------------------------------------------------------------- live events

const clients = new Set();
let stateTimer = null;
let pendingLog = [];
let logTimer = null;

function send(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

ingest.on('change', () => {
  if (stateTimer) return;
  stateTimer = setTimeout(() => {
    stateTimer = null;
    const snap = ingest.snapshot();
    for (const res of clients) send(res, 'state', snap);
  }, 250);
});

ingest.on('log', (line) => {
  pendingLog.push(line);
  if (logTimer) return;
  logTimer = setTimeout(() => {
    logTimer = null;
    const lines = pendingLog;
    pendingLog = [];
    for (const res of clients) send(res, 'log', { lines });
  }, 250);
});

// ------------------------------------------------------------------- routes

app.get('/healthz', route(async (req, res) => {
  try {
    await db.ping();
    res.json({ ok: true, busy: ingest.busy });
  } catch (err) {
    res.status(503).json({ ok: false, error: err.message });
  }
}));

app.use(basicAuth);
app.use(express.json({ limit: '16kb' }));
app.use(express.static(path.join(__dirname, 'public'), { index: 'index.html' }));

app.get('/api/status', route(async (req, res) => {
  let database;
  try {
    database = await db.overview();
  } catch (err) {
    database = { error: err.message };
  }
  res.json({ ...ingest.snapshot(), database });
}));

app.get('/api/events', (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write('retry: 3000\n\n');
  send(res, 'state', ingest.snapshot());
  send(res, 'log', { lines: ingest.recentLog(), reset: true });
  clients.add(res);
  const ping = setInterval(() => res.write(': ping\n\n'), 25000);
  req.on('close', () => {
    clearInterval(ping);
    clients.delete(res);
  });
});

app.post('/api/ingest', requireJson, (req, res) => {
  const job = ingest.start({ force: Boolean(req.body && req.body.force) });
  res.status(202).json({ id: job.id });
});

app.post('/api/ingest/cancel', requireJson, (req, res) => {
  ingest.cancel();
  res.json({ ok: true });
});

app.post('/api/rollback', requireJson, route(async (req, res) => {
  await ingest.rollback();
  res.json({ ok: true });
}));

app.get('/api/search', route(async (req, res) => {
  res.json({ results: await db.search(req.query.q) });
}));

app.get('/api/frn/:frn', route(async (req, res) => {
  const record = await db.frnDetail(req.params.frn);
  if (!record) throw new HttpError(404, `FRN ${req.params.frn} not found`);
  res.json(record);
}));

app.get('/api/frn/:frn/pdf', route(async (req, res) => {
  const pdf = await db.frnPdf(req.params.frn);
  if (!pdf) throw new HttpError(404, `No PDF stored for FRN ${req.params.frn}`);
  const name = `FRN-${req.params.frn.replace(/[^0-9A-Za-z-]/g, '')}.pdf`;
  res.set('Content-Type', 'application/pdf');
  res.set('Content-Disposition', `inline; filename="${name}"`);
  res.send(pdf);
}));

app.get('/api/jobs/:id/log', (req, res, next) => {
  if (!/^[0-9]{8}-[0-9]{6}$/.test(req.params.id)) return next(new HttpError(400, 'Bad job id'));
  res.type('text/plain; charset=utf-8');
  res.sendFile(path.join(LOG_DIR, `${req.params.id}.log`), (err) => {
    if (err && !res.headersSent) next(new HttpError(404, 'Log not found'));
  });
});

app.use((err, req, res, next) => { // eslint-disable-line no-unused-vars
  let status = err.status || 500;
  let message = err.message;
  if (err.code === 'ER_NO_SUCH_TABLE' || err.code === 'ER_BAD_DB_ERROR') {
    status = 404;
    message = 'The live database has no FRT data yet. Run an ingest first.';
  }
  if (status >= 500) console.error(err);
  res.status(status).json({ error: message });
});

// -------------------------------------------------------------------- start

async function main() {
  console.log('Waiting for MySQL…');
  await db.waitForReady();
  await db.ensureLiveDatabase();
  if (await db.ensureReadonlyUser()) {
    console.log(`Read-only user "${config.readonlyUser}" has SELECT on ${config.liveDb}`);
  }
  await ingest.init();

  const server = app.listen(config.port, () => {
    console.log(`FRT admin UI listening on :${config.port} (live database: ${config.liveDb})`);
  });

  const shutdown = () => {
    console.log('Shutting down…');
    ingest.killChildren();
    for (const res of clients) res.end();
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch((err) => {
  console.error('Startup failed:', err);
  process.exit(1);
});
