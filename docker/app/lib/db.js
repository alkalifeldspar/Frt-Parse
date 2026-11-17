'use strict';

const mysql = require('mysql2/promise');
const config = require('./config');

const pool = mysql.createPool({
  ...config.db,
  charset: 'utf8mb4',
  connectionLimit: 5,
  dateStrings: true,
  supportBigNumbers: true,
});

// information_schema caches table statistics for 24h by default, which would
// show stale sizes right after a swap.
pool.on('connection', (conn) => {
  conn.query('SET SESSION information_schema_stats_expiry = 0');
});

function id(name) {
  if (!/^[A-Za-z0-9_]+$/.test(name)) throw new Error(`Invalid identifier: ${name}`);
  return `\`${name}\``;
}

async function waitForReady(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      await pool.query('select 1');
      return;
    } catch (err) {
      if (Date.now() > deadline) throw err;
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

async function ping() {
  await pool.query('select 1');
}

async function ensureLiveDatabase() {
  await pool.query(`create database if not exists ${id(config.liveDb)} default character set utf8mb4`);
}

/** Create or refresh the optional read-only account for consumers of the data. */
async function ensureReadonlyUser() {
  const { readonlyUser: user, readonlyPassword: pass } = config;
  if (!user || !pass) return false;
  await pool.query("CREATE USER IF NOT EXISTS ?@'%' IDENTIFIED BY ?", [user, pass]);
  await pool.query("ALTER USER ?@'%' IDENTIFIED BY ?", [user, pass]);
  // Database-level grant: survives the table renames done by every swap.
  await pool.query(`GRANT SELECT ON ${id(config.liveDb)}.* TO ?@'%'`, [user]);
  return true;
}

async function recreateDatabase(name) {
  await pool.query(`drop database if exists ${id(name)}`);
  await pool.query(`create database ${id(name)} default character set utf8mb4`);
}

async function dropDatabase(name) {
  await pool.query(`drop database if exists ${id(name)}`);
}

async function databaseSummary(name) {
  const [tables] = await pool.query(
    `select table_name as name, table_rows as rowsApprox, data_length + index_length as bytes
       from information_schema.tables
      where table_schema = ? and table_type = 'BASE TABLE'
      order by table_name`,
    [name]
  );
  const summary = {
    name,
    exists: tables.length > 0,
    tables: tables.map((t) => ({ name: t.name, rows: Number(t.rowsApprox), bytes: Number(t.bytes) })),
    bytes: tables.reduce((sum, t) => sum + Number(t.bytes), 0),
    frnCount: null,
  };
  if (tables.some((t) => t.name === 'dbo_frn')) {
    const [[row]] = await pool.query(`select count(*) as n from ${id(name)}.dbo_frn`);
    summary.frnCount = Number(row.n);
    const t = summary.tables.find((x) => x.name === 'dbo_frn');
    t.rows = summary.frnCount; // exact, cheap for this table
  }
  return summary;
}

async function overview() {
  const [[v]] = await pool.query('select version() as version');
  return {
    server: v.version,
    live: await databaseSummary(config.liveDb),
    previous: await databaseSummary(config.previousDb),
  };
}

async function search(term) {
  const q = String(term || '').trim().slice(0, 100);
  if (!q) return [];
  const like = q.replace(/[\\%_]/g, '\\$&') + '%';
  const [rows] = await pool.query(
    `select frn, make, model, manufacturer, type, action, legal_classification
       from ${id(config.liveDb)}.dbo_frn
      where frn = ? or make like ? or model like ? or manufacturer like ?
      order by make, model
      limit 100`,
    [q, like, like, like]
  );
  return rows;
}

async function frnDetail(frn) {
  const live = id(config.liveDb);
  const [[record]] = await pool.query(
    `select frn, make, model, manufacturer, level, type, action, country,
            legal_classification, serial_numbering, year_dates, importer
       from ${live}.dbo_frn where frn = ?`,
    [frn]
  );
  if (!record) return null;
  const [subEntries] = await pool.query(
    `select sub_frn, calibre, shots, barrel_length, legal_classification
       from ${live}.dbo_frnSubEntry where frn = ? order by id`,
    [frn]
  );
  const [notes] = await pool.query(
    `select note_key, note_value from ${live}.dbo_frnNote where frn = ? order by id`,
    [frn]
  );
  const [crossReferences] = await pool.query(
    `select ref_frn, description from ${live}.dbo_frnCrossReference where frn = ? order by id`,
    [frn]
  );
  const [pdf] = await pool.query(`select 1 from ${live}.dbo_frnPdf where frn = ? limit 1`, [frn]);
  return { ...record, subEntries, notes, crossReferences, hasPdf: pdf.length > 0 };
}

async function frnPdf(frn) {
  const [[row]] = await pool.query(
    `select pdf64 from ${id(config.liveDb)}.dbo_frnPdf where frn = ? limit 1`,
    [frn]
  );
  return row && row.pdf64 ? Buffer.from(row.pdf64, 'base64') : null;
}

module.exports = {
  waitForReady,
  ping,
  ensureLiveDatabase,
  ensureReadonlyUser,
  recreateDatabase,
  dropDatabase,
  overview,
  search,
  frnDetail,
  frnPdf,
};
