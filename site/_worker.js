const SESSION_COOKIE = "tradea_session";
const PASSWORD_ITERATIONS = 100_000;
const SESSION_DAYS = 7;
const LOGIN_WINDOW_SECONDS = 15 * 60;
const LOGIN_ATTEMPT_LIMIT = 8;
const encoder = new TextEncoder();

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function apiHeaders(extra = {}) {
  return {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    ...extra,
  };
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: apiHeaders(headers),
  });
}

function nowIso() {
  return new Date().toISOString();
}

function todayIso() {
  return nowIso().slice(0, 10);
}

function bytesToBase64(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function randomBytes(length) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return bytes;
}

function constantTimeEqual(left, right) {
  if (left.length !== right.length) return false;
  let different = 0;
  for (let index = 0; index < left.length; index += 1) {
    different |= left[index] ^ right[index];
  }
  return different === 0;
}

async function verifySetupToken(provided, configured) {
  const expected = String(configured || "");
  if (expected.length < 24) {
    throw new HttpError(503, "初始化口令尚未配置");
  }
  const [providedHash, expectedHash] = await Promise.all([
    sha256Hex(String(provided || "")),
    sha256Hex(expected),
  ]);
  return constantTimeEqual(
    encoder.encode(providedHash),
    encoder.encode(expectedHash),
  );
}

export function normalizeUsername(value) {
  const username = String(value || "").trim().toLowerCase();
  if (!/^[a-z0-9._-]{3,32}$/.test(username)) {
    throw new HttpError(400, "账号必须为3-32位字母、数字、点、下划线或短横线");
  }
  return username;
}

export function validatePassword(value, minimumLength = 12) {
  const password = String(value || "");
  if (password.length < minimumLength || password.length > 128) {
    throw new HttpError(400, `密码长度必须为${minimumLength}-128位`);
  }
  return password;
}

function validateDisplayName(value) {
  const displayName = String(value || "").trim();
  if (!displayName || displayName.length > 40) {
    throw new HttpError(400, "成员名称不能为空且不能超过40个字符");
  }
  return displayName;
}

export function validateStock(payload) {
  const code = String(payload?.code || "").trim();
  const name = String(payload?.name || "").trim();
  if (!/^\d{6}$/.test(code)) throw new HttpError(400, "股票代码必须为6位数字");
  if (!name || name.length > 40) throw new HttpError(400, "股票名称无效");
  return { code, name };
}

export function validateAnnotation(payload) {
  const note = String(payload?.note || "").trim();
  const patternLabel = String(payload?.patternLabel || "watch");
  const timeframe = String(payload?.timeframe || "daily");
  const sampleDate = String(payload?.sampleDate || todayIso());
  const confidence = Number(payload?.confidence ?? 3);
  const nominated = Boolean(payload?.nominated);
  if (note.length > 2000) throw new HttpError(400, "标注不能超过2000个字符");
  if (!new Set(["watch", "positive", "negative"]).has(patternLabel)) {
    throw new HttpError(400, "形态标签无效");
  }
  if (!new Set(["daily", "weekly"]).has(timeframe)) {
    throw new HttpError(400, "形态周期无效");
  }
  const parsedDate = new Date(`${sampleDate}T00:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(sampleDate)
    || Number.isNaN(parsedDate.getTime())
    || parsedDate.toISOString().slice(0, 10) !== sampleDate
  ) {
    throw new HttpError(400, "观察日期无效");
  }
  if (!Number.isInteger(confidence) || confidence < 1 || confidence > 5) {
    throw new HttpError(400, "置信度必须为1-5");
  }
  if (nominated && !note) throw new HttpError(400, "申请训练审核时必须填写标注");
  return { note, patternLabel, timeframe, sampleDate, confidence, nominated };
}

export async function hashPassword(
  password,
  salt = randomBytes(16),
  iterations = PASSWORD_ITERATIONS,
) {
  const normalizedPassword = validatePassword(password, 4);
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(normalizedPassword),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    key,
    256,
  );
  return {
    hash: bytesToBase64(new Uint8Array(bits)),
    salt: bytesToBase64(salt),
    iterations,
  };
}

export async function verifyPassword(password, stored) {
  const derived = await hashPassword(
    password,
    base64ToBytes(stored.salt),
    Number(stored.iterations),
  );
  return constantTimeEqual(
    base64ToBytes(derived.hash),
    base64ToBytes(stored.hash),
  );
}

async function sha256Hex(value) {
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", encoder.encode(value)),
  );
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function parseCookies(header) {
  const cookies = {};
  for (const part of String(header || "").split(";")) {
    const index = part.indexOf("=");
    if (index <= 0) continue;
    try {
      cookies[part.slice(0, index).trim()] = decodeURIComponent(
        part.slice(index + 1).trim(),
      );
    } catch {
      // Ignore malformed cookie values instead of failing the whole request.
    }
  }
  return cookies;
}

function sessionCookie(requestUrl, token, maxAge) {
  const secure = new URL(requestUrl).protocol === "https:" ? "; Secure" : "";
  return `${SESSION_COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secure}`;
}

async function readJson(request) {
  const declaredLength = Number(request.headers.get("Content-Length") || 0);
  if (declaredLength > 32_768) throw new HttpError(413, "请求内容过大");
  const text = await request.text();
  if (text.length > 32_768) throw new HttpError(413, "请求内容过大");
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    throw new HttpError(400, "JSON格式无效");
  }
}

function requireSameOrigin(request) {
  if (["GET", "HEAD"].includes(request.method)) return;
  const url = new URL(request.url);
  const origin = request.headers.get("Origin");
  const fetchSite = request.headers.get("Sec-Fetch-Site");
  if ((origin && origin !== url.origin) || fetchSite === "cross-site") {
    throw new HttpError(403, "拒绝跨站写入");
  }
}

async function setupRequired(db) {
  const row = await db.prepare("SELECT COUNT(*) AS count FROM users").first();
  return Number(row?.count || 0) === 0;
}

function publicUser(row) {
  return {
    id: row.id,
    username: row.username,
    displayName: row.displayName ?? row.display_name,
    role: row.role,
  };
}

async function currentUser(request, db) {
  const token = parseCookies(request.headers.get("Cookie"))[SESSION_COOKIE];
  if (!token) return null;
  const tokenHash = await sha256Hex(token);
  const row = await db
    .prepare(
      `SELECT u.id, u.username, u.display_name AS displayName, u.role
       FROM sessions s
       JOIN users u ON u.id = s.user_id
       WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0`,
    )
    .bind(tokenHash, nowIso())
    .first();
  return row ? publicUser(row) : null;
}

async function requireUser(request, db) {
  const user = await currentUser(request, db);
  if (!user) throw new HttpError(401, "请先登录");
  return user;
}

function requireAdmin(user) {
  if (user.role !== "admin") throw new HttpError(403, "需要管理员权限");
}

async function issueSession(request, db, user, status = 200) {
  const token = bytesToBase64(randomBytes(32));
  const tokenHash = await sha256Hex(token);
  const createdAt = nowIso();
  const expiresAt = new Date(
    Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000,
  ).toISOString();
  await db
    .prepare(
      "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
    )
    .bind(crypto.randomUUID(), user.id, tokenHash, createdAt, expiresAt)
    .run();
  return json(
    { user: publicUser(user) },
    status,
    { "Set-Cookie": sessionCookie(request.url, token, SESSION_DAYS * 86_400) },
  );
}

async function createUser(db, payload, role) {
  const username = normalizeUsername(payload.username);
  const displayName = validateDisplayName(payload.displayName);
  const password = validatePassword(payload.password, role === "admin" ? 12 : 4);
  const passwordData = await hashPassword(password);
  const timestamp = nowIso();
  const user = {
    id: crypto.randomUUID(),
    username,
    displayName,
    role,
  };
  await db
    .prepare(
      `INSERT INTO users
       (id, username, display_name, role, password_hash, password_salt,
        password_iterations, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      user.id,
      username,
      displayName,
      role,
      passwordData.hash,
      passwordData.salt,
      passwordData.iterations,
      timestamp,
      timestamp,
    )
    .run();
  return user;
}

async function registerFailedLogin(db, username) {
  const now = Math.floor(Date.now() / 1000);
  const cutoff = now - LOGIN_WINDOW_SECONDS;
  await db
    .prepare(
      `INSERT INTO login_attempts (username, attempts, window_started)
       VALUES (?, 1, ?)
       ON CONFLICT(username) DO UPDATE SET
         attempts = CASE WHEN login_attempts.window_started < ? THEN 1
                         ELSE login_attempts.attempts + 1 END,
         window_started = CASE WHEN login_attempts.window_started < ? THEN ?
                               ELSE login_attempts.window_started END`,
    )
    .bind(username, now, cutoff, cutoff, now)
    .run();
}

async function checkLoginLimit(db, username) {
  const row = await db
    .prepare("SELECT attempts, window_started FROM login_attempts WHERE username = ?")
    .bind(username)
    .first();
  const cutoff = Math.floor(Date.now() / 1000) - LOGIN_WINDOW_SECONDS;
  if (row && Number(row.window_started) >= cutoff && Number(row.attempts) >= LOGIN_ATTEMPT_LIMIT) {
    throw new HttpError(429, "登录尝试过多，请稍后再试");
  }
}

async function listFavorites(db, user) {
  const result = await db
    .prepare(
      `SELECT s.code, s.name, s.created_at, s.updated_at,
              u.display_name AS added_by,
              (SELECT COUNT(*) FROM member_favorites f
               WHERE f.stock_code = s.code) AS member_count,
              EXISTS(SELECT 1 FROM member_favorites mine
                     WHERE mine.stock_code = s.code AND mine.user_id = ?) AS mine,
              (SELECT COUNT(*) FROM annotations a
               WHERE a.stock_code = s.code) AS annotation_count
       FROM shared_stocks s
       JOIN users u ON u.id = s.first_added_by
       WHERE EXISTS(SELECT 1 FROM member_favorites f WHERE f.stock_code = s.code)
          OR EXISTS(SELECT 1 FROM annotations a WHERE a.stock_code = s.code)
       ORDER BY s.updated_at DESC, s.code ASC`,
    )
    .bind(user.id)
    .all();
  const favorites = (result.results || []).map((row) => ({
    code: row.code,
    name: row.name,
    addedBy: row.added_by,
    memberCount: Number(row.member_count || 0),
    annotationCount: Number(row.annotation_count || 0),
    mine: Boolean(row.mine),
    updatedAt: row.updated_at,
  }));

  if (user.role !== "admin") return favorites;

  const collectorResult = await db
    .prepare(
      `SELECT f.stock_code AS code,
              u.id, u.username, u.display_name AS display_name,
              u.role, u.disabled
       FROM member_favorites f
       JOIN users u ON u.id = f.user_id
       ORDER BY f.stock_code ASC, u.role ASC, u.username ASC`,
    )
    .all();
  const collectorsByCode = new Map();
  for (const row of collectorResult.results || []) {
    if (!collectorsByCode.has(row.code)) collectorsByCode.set(row.code, []);
    collectorsByCode.get(row.code).push({
      id: row.id,
      username: row.username,
      displayName: row.display_name,
      role: row.role,
      disabled: Boolean(row.disabled),
    });
  }

  return favorites.map((favorite) => ({
    ...favorite,
    collectors: collectorsByCode.get(favorite.code) || [],
  }));
}

function favoriteStatements(db, user, stock, timestamp) {
  return [
    db
      .prepare(
        `INSERT INTO shared_stocks
         (code, name, first_added_by, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(code) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at`,
      )
      .bind(stock.code, stock.name, user.id, timestamp, timestamp),
    db
      .prepare(
        `INSERT INTO member_favorites (user_id, stock_code, created_at)
         VALUES (?, ?, ?) ON CONFLICT(user_id, stock_code) DO NOTHING`,
      )
      .bind(user.id, stock.code, timestamp),
  ];
}

async function listAnnotations(db, code) {
  const result = await db
    .prepare(
      `SELECT a.id, a.user_id, u.display_name AS author, a.note,
              a.pattern_label, a.timeframe, a.sample_date, a.confidence,
              a.nominated, a.review_status, a.updated_at
       FROM annotations a JOIN users u ON u.id = a.user_id
       WHERE a.stock_code = ? ORDER BY a.updated_at DESC`,
    )
    .bind(code)
    .all();
  return (result.results || []).map((row) => ({
    id: row.id,
    userId: row.user_id,
    author: row.author,
    note: row.note,
    patternLabel: row.pattern_label,
    timeframe: row.timeframe,
    sampleDate: row.sample_date,
    confidence: Number(row.confidence),
    nominated: Boolean(row.nominated),
    reviewStatus: row.review_status,
    updatedAt: row.updated_at,
  }));
}

async function routeApi(request, env) {
  if (!env.DB) throw new HttpError(503, "成员数据库尚未绑定");
  const db = env.DB;
  const url = new URL(request.url);
  const path = url.pathname;
  requireSameOrigin(request);

  if (request.method === "GET" && path === "/api/auth/status") {
    return json({
      setupRequired: await setupRequired(db),
      setupReady: String(env.SETUP_TOKEN || "").length >= 24,
    });
  }

  if (request.method === "GET" && path === "/api/auth/me") {
    return json({
      user: await currentUser(request, db),
      setupRequired: await setupRequired(db),
      setupReady: String(env.SETUP_TOKEN || "").length >= 24,
    });
  }

  if (request.method === "POST" && path === "/api/auth/setup") {
    if (!(await setupRequired(db))) throw new HttpError(409, "管理员已经初始化");
    const payload = await readJson(request);
    if (!(await verifySetupToken(payload.setupToken, env.SETUP_TOKEN))) {
      throw new HttpError(403, "初始化口令错误");
    }
    try {
      const user = await createUser(db, payload, "admin");
      return issueSession(request, db, user, 201);
    } catch (error) {
      if (String(error?.message || "").includes("UNIQUE")) {
        throw new HttpError(409, "管理员已经初始化");
      }
      throw error;
    }
  }

  if (request.method === "POST" && path === "/api/auth/login") {
    const payload = await readJson(request);
    const username = normalizeUsername(payload.username);
    const password = validatePassword(payload.password, 4);
    await checkLoginLimit(db, username);
    const row = await db
      .prepare(
        `SELECT id, username, display_name AS displayName, role,
                password_hash AS hash, password_salt AS salt,
                password_iterations AS iterations, disabled
         FROM users WHERE username = ?`,
      )
      .bind(username)
      .first();
    const valid = row && !row.disabled && (await verifyPassword(password, row));
    if (!valid) {
      await registerFailedLogin(db, username);
      throw new HttpError(401, "账号或密码错误");
    }
    await db.prepare("DELETE FROM login_attempts WHERE username = ?").bind(username).run();
    await db.prepare("DELETE FROM sessions WHERE expires_at <= ?").bind(nowIso()).run();
    return issueSession(request, db, row);
  }

  if (request.method === "POST" && path === "/api/auth/logout") {
    const token = parseCookies(request.headers.get("Cookie"))[SESSION_COOKIE];
    if (token) {
      await db
        .prepare("DELETE FROM sessions WHERE token_hash = ?")
        .bind(await sha256Hex(token))
        .run();
    }
    return json(
      { ok: true },
      200,
      { "Set-Cookie": sessionCookie(request.url, "", 0) },
    );
  }

  const user = await requireUser(request, db);

  if (path === "/api/members" && request.method === "GET") {
    requireAdmin(user);
    const result = await db
      .prepare(
        `SELECT id, username, display_name AS displayName, role, disabled, created_at AS createdAt
         FROM users ORDER BY role ASC, created_at ASC`,
      )
      .all();
    return json({ members: result.results || [] });
  }

  if (path === "/api/members" && request.method === "POST") {
    requireAdmin(user);
    try {
      const member = await createUser(db, await readJson(request), "member");
      return json({ member: publicUser(member) }, 201);
    } catch (error) {
      if (String(error?.message || "").includes("UNIQUE")) {
        throw new HttpError(409, "账号已存在");
      }
      throw error;
    }
  }

  const memberMatch = path.match(/^\/api\/members\/([0-9a-f-]+)$/i);
  if (memberMatch && request.method === "PATCH") {
    requireAdmin(user);
    if (memberMatch[1] === user.id) throw new HttpError(400, "不能停用当前管理员");
    const payload = await readJson(request);
    if (typeof payload.disabled !== "boolean") throw new HttpError(400, "停用状态无效");
    const result = await db
      .prepare("UPDATE users SET disabled = ?, updated_at = ? WHERE id = ? AND role = 'member'")
      .bind(payload.disabled ? 1 : 0, nowIso(), memberMatch[1])
      .run();
    if (!result.meta?.changes) throw new HttpError(404, "成员不存在");
    return json({ ok: true });
  }

  if (path === "/api/favorites" && request.method === "GET") {
    return json({ favorites: await listFavorites(db, user) });
  }

  if (path === "/api/favorites" && request.method === "POST") {
    const stock = validateStock(await readJson(request));
    await db.batch(favoriteStatements(db, user, stock, nowIso()));
    return json({ favorites: await listFavorites(db, user) }, 201);
  }

  if (path === "/api/favorites/import" && request.method === "POST") {
    const payload = await readJson(request);
    if (!Array.isArray(payload.items) || payload.items.length > 50) {
      throw new HttpError(400, "一次最多导入50只收藏");
    }
    const timestamp = nowIso();
    const statements = payload.items.flatMap((item) =>
      favoriteStatements(db, user, validateStock(item), timestamp),
    );
    if (statements.length) await db.batch(statements);
    return json({ favorites: await listFavorites(db, user) });
  }

  const favoriteMatch = path.match(/^\/api\/favorites\/(\d{6})$/);
  if (favoriteMatch && request.method === "DELETE") {
    await db
      .prepare("DELETE FROM member_favorites WHERE user_id = ? AND stock_code = ?")
      .bind(user.id, favoriteMatch[1])
      .run();
    return json({ favorites: await listFavorites(db, user) });
  }

  const annotationMatch = path.match(/^\/api\/annotations\/(\d{6})$/);
  if (annotationMatch && request.method === "GET") {
    return json({ annotations: await listAnnotations(db, annotationMatch[1]) });
  }

  if (annotationMatch && request.method === "PUT") {
    const payload = await readJson(request);
    const stock = validateStock({ code: annotationMatch[1], name: payload.name });
    const annotation = validateAnnotation(payload);
    const timestamp = nowIso();
    const reviewStatus = annotation.nominated ? "pending" : "not_requested";
    await db.batch([
      ...favoriteStatements(db, user, stock, timestamp),
      db
        .prepare(
          `INSERT INTO annotations
           (id, user_id, stock_code, note, pattern_label, timeframe,
            sample_date, confidence, nominated, review_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, stock_code) DO UPDATE SET
             note = excluded.note,
             pattern_label = excluded.pattern_label,
             timeframe = excluded.timeframe,
             sample_date = excluded.sample_date,
             confidence = excluded.confidence,
             nominated = excluded.nominated,
             review_status = excluded.review_status,
             reviewed_by = NULL,
             reviewed_at = NULL,
             updated_at = excluded.updated_at`,
        )
        .bind(
          crypto.randomUUID(),
          user.id,
          stock.code,
          annotation.note,
          annotation.patternLabel,
          annotation.timeframe,
          annotation.sampleDate,
          annotation.confidence,
          annotation.nominated ? 1 : 0,
          reviewStatus,
          timestamp,
          timestamp,
        ),
    ]);
    return json({ annotations: await listAnnotations(db, stock.code) });
  }

  if (path === "/api/training" && request.method === "GET") {
    requireAdmin(user);
    const result = await db
      .prepare(
        `SELECT a.id, a.stock_code AS code, s.name, u.display_name AS author,
                a.note, a.pattern_label AS patternLabel, a.timeframe,
                a.sample_date AS sampleDate, a.confidence,
                a.review_status AS reviewStatus, a.updated_at AS updatedAt
         FROM annotations a
         JOIN shared_stocks s ON s.code = a.stock_code
         JOIN users u ON u.id = a.user_id
         WHERE a.nominated = 1
         ORDER BY CASE a.review_status WHEN 'pending' THEN 0 ELSE 1 END,
                  a.updated_at DESC`,
      )
      .all();
    return json({ feedback: result.results || [] });
  }

  const reviewMatch = path.match(/^\/api\/training\/([0-9a-f-]+)\/review$/i);
  if (reviewMatch && request.method === "PUT") {
    requireAdmin(user);
    const payload = await readJson(request);
    if (!new Set(["approved", "rejected"]).has(payload.status)) {
      throw new HttpError(400, "审核状态无效");
    }
    const result = await db
      .prepare(
        `UPDATE annotations
         SET review_status = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
         WHERE id = ? AND nominated = 1`,
      )
      .bind(payload.status, user.id, nowIso(), nowIso(), reviewMatch[1])
      .run();
    if (!result.meta?.changes) throw new HttpError(404, "训练反馈不存在");
    return json({ ok: true });
  }

  if (path === "/api/training/export" && request.method === "GET") {
    requireAdmin(user);
    const result = await db
      .prepare(
        `SELECT a.id AS annotation_id, a.stock_code AS code, s.name,
                a.pattern_label AS label, a.timeframe,
                a.sample_date, a.confidence, a.note,
                submitter.display_name AS submitted_by,
                reviewer.display_name AS approved_by,
                a.reviewed_at AS approved_at
         FROM annotations a
         JOIN shared_stocks s ON s.code = a.stock_code
         JOIN users submitter ON submitter.id = a.user_id
         JOIN users reviewer ON reviewer.id = a.reviewed_by
         WHERE a.nominated = 1 AND a.review_status = 'approved'
         ORDER BY a.reviewed_at ASC, a.stock_code ASC`,
      )
      .all();
    const payload = {
      schema_version: 1,
      exported_at: nowIso(),
      source: "TradeA member-reviewed annotations",
      samples: result.results || [],
    };
    return new Response(JSON.stringify(payload, null, 2), {
      headers: apiHeaders({
        "Content-Disposition": `attachment; filename="tradea-training-feedback-${todayIso()}.json"`,
      }),
    });
  }

  throw new HttpError(404, "接口不存在");
}

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/")) {
    if (!env.ASSETS?.fetch) return new Response("Not found", { status: 404 });
    return env.ASSETS.fetch(request);
  }
  try {
    return await routeApi(request, env);
  } catch (error) {
    if (error instanceof HttpError) return json({ error: error.message }, error.status);
    console.error("TradeA member API failure", {
      path: url.pathname,
      message: String(error?.message || error),
    });
    return json({ error: "成员服务暂时不可用" }, 500);
  }
}

export default {
  fetch: handleRequest,
};
