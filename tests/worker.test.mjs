import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import {
  HttpError,
  handleRequest,
  hashPassword,
  normalizeUsername,
  parseCookies,
  validateAnnotation,
  validatePassword,
  validateStock,
  verifyPassword,
} from "../site/_worker.js";

class TestD1Statement {
  constructor(database, sql, values = []) {
    this.database = database;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    return new TestD1Statement(this.database, this.sql, values);
  }

  async first() {
    return this.database.prepare(this.sql).get(...this.values) || null;
  }

  async all() {
    return { results: this.database.prepare(this.sql).all(...this.values) };
  }

  runSync() {
    const result = this.database.prepare(this.sql).run(...this.values);
    return { meta: { changes: Number(result.changes) } };
  }

  async run() {
    return this.runSync();
  }
}

class TestD1Database {
  constructor() {
    this.database = new DatabaseSync(":memory:");
    this.database.exec(
      readFileSync(
        new URL("../db/migrations/0001_members.sql", import.meta.url),
        "utf8",
      ),
    );
  }

  prepare(sql) {
    return new TestD1Statement(this.database, sql);
  }

  async batch(statements) {
    this.database.exec("BEGIN");
    try {
      const results = statements.map((statement) => statement.runSync());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

async function apiRequest(env, path, { method = "GET", body, cookie } = {}) {
  const headers = { Origin: "https://tradea.test" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (cookie) headers.Cookie = cookie;
  return handleRequest(
    new Request(`https://tradea.test${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    env,
  );
}

function responseCookie(response) {
  return response.headers.get("Set-Cookie").split(";", 1)[0];
}

test("password hashing uses salt and verifies without storing plaintext", async () => {
  const first = await hashPassword("long-password-123", new Uint8Array(16).fill(1));
  const second = await hashPassword("long-password-123", new Uint8Array(16).fill(2));

  assert.notEqual(first.hash, second.hash);
  assert.equal(await verifyPassword("long-password-123", first), true);
  assert.equal(await verifyPassword("wrong-password-123", first), false);
  assert.equal(first.iterations, 100_000);
});

test("member and stock inputs are normalized and bounded", () => {
  assert.equal(normalizeUsername("  Robin.Hood  "), "robin.hood");
  assert.equal(validatePassword("123456789012"), "123456789012");
  assert.equal(validatePassword("1234", 4), "1234");
  assert.deepEqual(validateStock({ code: "300300", name: "海峡创新" }), {
    code: "300300",
    name: "海峡创新",
  });
  assert.throws(() => normalizeUsername("ab"), HttpError);
  assert.throws(() => validatePassword("short"), HttpError);
  assert.throws(() => validatePassword("123", 4), HttpError);
  assert.throws(() => validateStock({ code: "30030", name: "测试" }), HttpError);
});

test("training annotations require valid labels and a note when nominated", () => {
  assert.deepEqual(
    validateAnnotation({
      note: "周线右侧开始放量",
      patternLabel: "positive",
      timeframe: "weekly",
      sampleDate: "2026-09-18",
      confidence: 4,
      nominated: true,
    }),
    {
      note: "周线右侧开始放量",
      patternLabel: "positive",
      timeframe: "weekly",
      sampleDate: "2026-09-18",
      confidence: 4,
      nominated: true,
    },
  );
  assert.throws(
    () => validateAnnotation({ nominated: true, note: "" }),
    /必须填写标注/,
  );
  assert.throws(
    () => validateAnnotation({ patternLabel: "buy" }),
    /形态标签无效/,
  );
  assert.throws(
    () => validateAnnotation({ sampleDate: "2026-02-31" }),
    /观察日期无效/,
  );
});

test("cookie parser ignores malformed entries", () => {
  assert.deepEqual(parseCookies("a=1; tradea_session=abc%2B123; bad=%E0%A4; broken"), {
    a: "1",
    tradea_session: "abc+123",
  });
});

test("API fails closed when the D1 binding is absent", async () => {
  const response = await handleRequest(
    new Request("https://tradea.example/api/auth/status"),
    {},
  );

  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "成员数据库尚未绑定" });
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("initial administrator setup requires a configured secret", async () => {
  const env = { DB: new TestD1Database() };
  let response = await apiRequest(env, "/api/auth/status");
  assert.deepEqual(await response.json(), {
    setupRequired: true,
    setupReady: false,
  });

  response = await apiRequest(env, "/api/auth/setup", {
    method: "POST",
    body: {
      username: "owner",
      displayName: "负责人",
      password: "owner-password-123",
      setupToken: "wrong-token-that-is-long-enough",
    },
  });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "初始化口令尚未配置" });
});

test("non-API requests continue to the Pages asset binding", async () => {
  const response = await handleRequest(
    new Request("https://tradea.example/"),
    {
      ASSETS: {
        fetch: async () => new Response("report", { status: 200 }),
      },
    },
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "report");
});

test("member workflow shares favorites and exports only admin-approved feedback", async () => {
  const setupToken = "test-setup-token-1234567890";
  const env = { DB: new TestD1Database(), SETUP_TOKEN: setupToken };

  let response = await apiRequest(env, "/api/auth/status");
  assert.deepEqual(await response.json(), {
    setupRequired: true,
    setupReady: true,
  });

  response = await apiRequest(env, "/api/auth/setup", {
    method: "POST",
    body: {
      username: "owner",
      displayName: "负责人",
      password: "owner-password-123",
      setupToken,
    },
  });
  assert.equal(response.status, 201);
  const adminCookie = responseCookie(response);

  response = await apiRequest(env, "/api/members", {
    method: "POST",
    cookie: adminCookie,
    body: {
      username: "member1",
      displayName: "成员一",
      password: "1234",
    },
  });
  assert.equal(response.status, 201);

  response = await apiRequest(env, "/api/auth/login", {
    method: "POST",
    body: { username: "member1", password: "1234" },
  });
  assert.equal(response.status, 200);
  const memberCookie = responseCookie(response);

  response = await apiRequest(env, "/api/members", { cookie: memberCookie });
  assert.equal(response.status, 403);

  response = await apiRequest(env, "/api/favorites", {
    method: "POST",
    cookie: memberCookie,
    body: { code: "300300", name: "海峡创新" },
  });
  assert.equal(response.status, 201);
  let payload = await response.json();
  assert.equal(payload.favorites[0].code, "300300");
  assert.equal(payload.favorites[0].mine, true);
  assert.equal(Object.hasOwn(payload.favorites[0], "collectors"), false);

  response = await apiRequest(env, "/api/favorites", { cookie: adminCookie });
  assert.equal(response.status, 200);
  payload = await response.json();
  assert.equal(payload.favorites[0].mine, false);
  assert.deepEqual(
    payload.favorites[0].collectors.map((collector) => ({
      username: collector.username,
      displayName: collector.displayName,
      role: collector.role,
      disabled: collector.disabled,
    })),
    [
      {
        username: "member1",
        displayName: "成员一",
        role: "member",
        disabled: false,
      },
    ],
  );

  response = await apiRequest(env, "/api/annotations/300300", {
    method: "PUT",
    cookie: memberCookie,
    body: {
      name: "海峡创新",
      note: "周线右侧量能开始放大",
      patternLabel: "positive",
      timeframe: "weekly",
      sampleDate: "2026-09-18",
      confidence: 4,
      nominated: true,
    },
  });
  assert.equal(response.status, 200);
  payload = await response.json();
  assert.equal(payload.annotations[0].reviewStatus, "pending");

  response = await apiRequest(env, "/api/training", { cookie: adminCookie });
  payload = await response.json();
  assert.equal(payload.feedback.length, 1);
  const feedbackId = payload.feedback[0].id;

  response = await apiRequest(env, `/api/training/${feedbackId}/review`, {
    method: "PUT",
    cookie: adminCookie,
    body: { status: "approved" },
  });
  assert.equal(response.status, 200);

  response = await apiRequest(env, "/api/training/export", {
    cookie: adminCookie,
  });
  assert.equal(response.status, 200);
  payload = await response.json();
  assert.equal(payload.schema_version, 1);
  assert.equal(payload.samples.length, 1);
  assert.equal(payload.samples[0].code, "300300");
  assert.equal(payload.samples[0].label, "positive");
  assert.equal(payload.samples[0].timeframe, "weekly");
});

test("cross-site mutation requests are rejected", async () => {
  const env = {
    DB: new TestD1Database(),
    SETUP_TOKEN: "test-setup-token-1234567890",
  };
  const response = await handleRequest(
    new Request("https://tradea.test/api/auth/setup", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "https://evil.example",
        "Sec-Fetch-Site": "cross-site",
      },
      body: JSON.stringify({
        username: "owner",
        displayName: "负责人",
        password: "owner-password-123",
        setupToken: "test-setup-token-1234567890",
      }),
    }),
    env,
  );

  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "拒绝跨站写入" });
});

test("member UI includes admin-only favorite ownership details", () => {
  const script = readFileSync(new URL("../site/member.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../site/member.css", import.meta.url), "utf8");

  assert.match(script, /id="member-admin-favorites-section"/);
  assert.match(script, /function renderMemberFavoriteDetails\(\)/);
  assert.match(script, /favorite\.collectors/);
  assert.match(script, /收藏人/);
  assert.match(styles, /\.member-favorite-stock-list/);
});

test("member UI identifies the trusted local owner runtime", () => {
  const script = readFileSync(new URL("../site/member.js", import.meta.url), "utf8");

  assert.match(script, /Boolean\(window\.__TRADEA_LOCAL_OWNER__\)/);
  assert.match(script, /主管理员连接中/);
  assert.match(script, /主管理员 · 本机自动登录/);
  assert.match(script, /logoutButton\.hidden = LOCAL_OWNER_MODE/);
  assert.match(script, /if \(LOCAL_OWNER_MODE\) return;/);
});
