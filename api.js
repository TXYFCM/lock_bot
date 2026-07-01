// api.js - API 调用层
// 封装 Lock Bot 和 Monquery 的 HTTP 调用，纯异步函数，不含业务逻辑

// 本地代理路径（通过 proxy.js 解决 CORS）
// 直接改为内网地址也可（如果网络/CORS 条件允许）
const MONQUERY_BASE = '/monquery';
const LOCKBOT_BASE = '/lockbot';

// 两批节点使用不同的 namespace
const CLUSTER_BACKUP = 'wxtky02-p800-backup-8nic-vd';
const CLUSTER_NON_BACKUP = 'wxtky02-p800-8nic-vd';

// 非 backup namespace 的节点
const NON_BACKUP_NODES = [32, 34, 35, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51];

// 有监控数据的节点（排除 node13, node14, node17 为故障机）
const MONITORED_NODES = Array.from({ length: 51 }, (_, i) => i + 1)
  .filter(n => ![13, 14, 17].includes(n));

function buildNamespace(nodeNum) {
  const cluster = NON_BACKUP_NODES.includes(nodeNum) ? CLUSTER_NON_BACKUP : CLUSTER_BACKUP;
  return `${cluster}-node${nodeNum}.wxtky02`;
}

// 核心指标：整机级先展示，卡级指标后续渐进补齐
const MONQUERY_NODE_ITEMS = ['XPU_AVERAGE_UTILIZATION'];
const MONQUERY_CARD_XPU_ITEMS = Array.from({ length: 8 }, (_, c) => `XPU${c}_XPU_UTILIZATION`);
const MONQUERY_CARD_MEM_ITEMS = Array.from({ length: 8 }, (_, c) => `XPU${c}_MEM_UTILIZATION`);
const MONQUERY_CARD_ITEMS = [...MONQUERY_CARD_XPU_ITEMS, ...MONQUERY_CARD_MEM_ITEMS];
const MONQUERY_ITEMS = [...MONQUERY_NODE_ITEMS, ...MONQUERY_CARD_ITEMS];

function fetchWithTimeout(url, options = {}, timeout = 30000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  return fetch(url, { ...options, signal: controller.signal })
    .finally(() => clearTimeout(id));
}

/**
 * Lock Bot 登录，返回 JWT access_token
 */
export async function loginLockBot(username, password) {
  const resp = await fetchWithTimeout(`${LOCKBOT_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) throw new Error(`Login failed: ${resp.status} ${resp.statusText}`);
  const data = await resp.json();
  return data.access_token;
}

/**
 * 获取当前用户的所有 Bot 列表
 */
export async function fetchLockBotList(token) {
  const resp = await fetchWithTimeout(`${LOCKBOT_BASE}/api/bots`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Fetch bot list failed: ${resp.status}`);
  return resp.json();
}

/**
 * 获取单个 Bot 的状态（节点/设备占用情况）
 */
export async function fetchLockBotState(botId, token) {
  const resp = await fetchWithTimeout(`${LOCKBOT_BASE}/api/bots/${botId}/state`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Fetch bot state failed: ${resp.status}`);
  return resp.json();
}

/**
 * 批量查询所有 Bot 的状态
 */
export async function fetchAllBotStates(token) {
  const resp = await fetchWithTimeout(`${LOCKBOT_BASE}/api/bots/running-states`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`Fetch all bot states failed: ${resp.status}`);
  return resp.json();
}

/**
 * 查询 Bot 的当天历史占用记录
 * @param {number} botId - Bot ID
 * @param {string} date - 日期 "YYYY-MM-DD"
 * @param {string} token - JWT token
 * @returns {Promise<Array>} 占用记录数组 [{node_key, user_id, lock_mode, start_time, end_time, duration_seconds}]
 */
export async function fetchLockBotOccupancy(botId, date, token) {
  const resp = await fetchWithTimeout(
    `${LOCKBOT_BASE}/api/bots/${botId}/occupancy?date=${encodeURIComponent(date)}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!resp.ok) throw new Error(`Fetch occupancy failed: ${resp.status}`);
  return resp.json();
}

/**
 * 查询指定节点 + 指标的 Monquery 数据
 * @param {string} start - 起始时间 YYYYMMDDHHmmss
 * @param {string} end   - 结束时间 YYYYMMDDHHmmss
 * @param {number[]} nodeNums - 节点编号
 * @param {string[]} items - 指标名
 * @returns {Promise<Array>} monquery data[] 数组
 */
async function fetchMonqueryItems(start, end, nodeNums, items) {
  if (!nodeNums.length || !items.length) return [];
  const namespaces = nodeNums.map(buildNamespace).join(',');
  const url = `${MONQUERY_BASE}/monquery/getHistoryitemdata` +
    `?namespaces=${encodeURIComponent(namespaces)}` +
    `&items=${encodeURIComponent(items.join(','))}` +
    `&start=${start}&end=${end}&interval=300`;
  const resp = await fetchWithTimeout(url);
  if (!resp.ok) throw new Error(`Monquery fetch failed: ${resp.status}`);
  const data = await resp.json();
  if (!data.success) throw new Error(`Monquery error: ${data.message}`);
  return data.data || [];
}

function makeNodeBatches(batchSize) {
  const batches = [];
  for (let i = 0; i < MONITORED_NODES.length; i += batchSize) {
    batches.push(MONITORED_NODES.slice(i, i + batchSize));
  }
  return batches;
}

/**
 * 先查询整机级 XPU 平均利用率，用于快速首屏渲染
 */
export async function fetchMonqueryNodeUtilization(start, end) {
  const results = await Promise.all(
    makeNodeBatches(24).map(nodeNums => fetchMonqueryItems(start, end, nodeNums, MONQUERY_NODE_ITEMS))
  );
  return results.flat();
}

/**
 * 分批查询卡级 XPU/显存指标，调用方可按批次渐进渲染
 */
export async function* fetchMonqueryCardUtilizationBatches(start, end, options = {}) {
  const batchSize = options.batchSize || 8;
  const pending = makeNodeBatches(batchSize).map((nodeNums, index) => {
    const entry = { index, nodeNums, promise: null };
    entry.promise = fetchMonqueryItems(start, end, nodeNums, MONQUERY_CARD_ITEMS)
      .then(data => ({ entry, nodeNums, data }));
    return entry;
  });
  while (pending.length) {
    const batch = await Promise.race(pending.map(entry => entry.promise));
    const idx = pending.indexOf(batch.entry);
    if (idx >= 0) pending.splice(idx, 1);
    yield { nodeNums: batch.nodeNums, data: batch.data };
  }
}

/**
 * 批量查询 48 个节点的完整监控数据（保留兼容 average.html 等旧调用）
 * @param {string} start - 起始时间 YYYYMMDDHHmmss
 * @param {string} end   - 结束时间 YYYYMMDDHHmmss
 * @returns {Promise<Array>} monquery data[] 数组
 */
export async function fetchMonqueryUtilization(start, end) {
  const results = await Promise.all(
    makeNodeBatches(16).map(nodeNums => fetchMonqueryItems(start, end, nodeNums, MONQUERY_ITEMS))
  );
  return results.flat();
}

/**
 * 判断错误是否为 AbortController 超时/中止，用于区分主动取消和真实网络异常
 */
export function isAbortError(err) {
  return err && err.name === 'AbortError';
}

export { MONITORED_NODES, MONQUERY_ITEMS, MONQUERY_NODE_ITEMS, MONQUERY_CARD_ITEMS };
