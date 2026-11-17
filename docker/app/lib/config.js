'use strict';

// All runtime configuration comes from environment variables (see docker/.env.example).

const env = process.env;

function required(name) {
  const value = env[name];
  if (!value) {
    console.error(`Missing required environment variable ${name}`);
    process.exit(1);
  }
  return value;
}

function int(name, fallback) {
  const n = parseInt(env[name], 10);
  return Number.isFinite(n) ? n : fallback;
}

function bool(name, fallback) {
  if (env[name] === undefined || env[name] === '') return fallback;
  return /^(1|true|yes|on)$/i.test(env[name]);
}

function dbName(name, fallback) {
  const value = env[name] || fallback;
  if (!/^[A-Za-z0-9_]{1,48}$/.test(value)) {
    console.error(`${name} must contain only letters, digits and underscores (got "${value}")`);
    process.exit(1);
  }
  return value;
}

const liveDb = dbName('FRT_DB_NAME', 'FRT');
const ratio = parseFloat(env.MIN_ROW_RATIO);

module.exports = {
  port: int('UI_INTERNAL_PORT', 8080),
  uiUser: env.UI_USERNAME || 'admin',
  uiPassword: required('UI_PASSWORD'),

  db: {
    host: env.DB_HOST || '127.0.0.1',
    port: int('DB_PORT', 3306),
    user: 'root',
    password: required('MYSQL_ROOT_PASSWORD'),
  },
  liveDb,
  stagingDb: `${liveDb}_staging`,
  previousDb: `${liveDb}_previous`,
  readonlyUser: env.FRT_READONLY_USER || '',
  readonlyPassword: env.FRT_READONLY_PASSWORD || '',

  dataDir: env.DATA_DIR || '/data',
  scriptsDir: env.FRT_SCRIPTS_DIR || '/app/frt',
  python: env.PYTHON || '/opt/venv/bin/python',

  parseWorkers: int('PARSE_WORKERS', 1),
  splitWriters: int('SPLIT_WRITERS', 2),
  minRowRatio: Number.isFinite(ratio) ? ratio : 0.9,
  keepPrevious: bool('KEEP_PREVIOUS', true),
};
