(() => {
  "use strict";

  const FAVORITE_KEY = "ashare-screener:favorites:v1";
  const LOCAL_BACKUP_KEY = "tradea:local-favorites-backup:v1";
  const CLOUD_MODE_KEY = "tradea:cloud-favorites-user:v1";
  const state = {
    user: null,
    setupRequired: false,
    setupReady: false,
    serviceAvailable: true,
    favorites: [],
    members: [],
    feedback: [],
    currentStock: null,
  };

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    const contentType = response.headers.get("Content-Type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : null;
    if (!response.ok) {
      throw new ApiError(
        response.status,
        payload?.error || "成员服务尚未连接",
      );
    }
    return payload;
  }

  function createButton(text, className = "member-button") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    return button;
  }

  function setMessage(target, message = "", type = "") {
    target.textContent = message;
    target.className = `member-message${type ? ` ${type}` : ""}`;
  }

  function notify(message, type = "") {
    const region = $("#member-live-region");
    region.hidden = false;
    region.textContent = message;
    region.className = `member-live-region${type ? ` ${type}` : ""}`;
    window.setTimeout(() => {
      region.hidden = true;
    }, 2600);
  }

  function openDialog(dialog) {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeDialog(dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function buildUi() {
    const headerRow = $(".header-row");
    const titleBlock = headerRow?.firstElementChild;
    if (headerRow && titleBlock) {
      const actions = document.createElement("div");
      actions.className = "member-header-actions";
      for (const child of Array.from(headerRow.children).slice(1)) {
        actions.appendChild(child);
      }
      const accountButton = createButton("成员登录", "member-account-button");
      accountButton.id = "member-account-button";
      accountButton.addEventListener("click", openMemberCenter);
      actions.appendChild(accountButton);
      headerRow.appendChild(actions);
    }

    const memberDialog = document.createElement("dialog");
    memberDialog.id = "member-dialog";
    memberDialog.className = "member-dialog";
    memberDialog.innerHTML = `
      <div class="member-dialog-shell">
        <div class="member-dialog-head">
          <h2>成员中心</h2>
          <button type="button" class="member-close-button" aria-label="关闭" title="关闭">×</button>
        </div>
        <div class="member-dialog-body">
          <p id="member-dialog-message" class="member-message" aria-live="polite"></p>
          <div id="member-auth-view">
            <form id="member-setup-form" class="member-form" hidden>
              <label>成员名称<input name="displayName" maxlength="40" autocomplete="name" required></label>
              <label>管理员账号<input name="username" minlength="3" maxlength="32" autocomplete="username" required></label>
              <label class="member-field-wide">管理员密码<input name="password" type="password" minlength="12" maxlength="128" autocomplete="new-password" required></label>
              <label class="member-field-wide">初始化口令<input name="setupToken" type="password" minlength="24" maxlength="256" autocomplete="off" required></label>
              <div class="member-form-actions"><button class="member-button primary" type="submit">创建管理员</button></div>
            </form>
            <form id="member-login-form" class="member-form" hidden>
              <label>账号<input name="username" minlength="3" maxlength="32" autocomplete="username" required></label>
              <label>密码<input name="password" type="password" minlength="12" maxlength="128" autocomplete="current-password" required></label>
              <div class="member-form-actions"><button class="member-button primary" type="submit">登录</button></div>
            </form>
          </div>
          <div id="member-session-view" hidden>
            <div class="member-identity">
              <div><strong id="member-display-name"></strong> <span id="member-role" class="member-role"></span></div>
              <div class="member-session-actions">
                <button type="button" id="member-import-local" class="member-button">导入本机收藏</button>
                <button type="button" id="member-refresh" class="member-button">刷新</button>
                <button type="button" id="member-logout" class="member-button">退出</button>
              </div>
            </div>
            <section class="member-section">
              <div class="member-section-head"><h3>共享收藏</h3><span id="shared-favorite-count" class="member-chip">0</span></div>
              <div id="shared-favorite-list" class="member-list"></div>
            </section>
            <section id="member-admin-section" class="member-section" hidden>
              <div class="member-section-head"><h3>成员管理</h3></div>
              <form id="member-create-form" class="member-form">
                <label>成员名称<input name="displayName" maxlength="40" required></label>
                <label>账号<input name="username" minlength="3" maxlength="32" required></label>
                <label class="member-field-wide">初始密码<input name="password" type="password" minlength="12" maxlength="128" autocomplete="new-password" required></label>
                <div class="member-form-actions"><button class="member-button primary" type="submit">创建成员</button></div>
              </form>
              <div id="member-list" class="member-list"></div>
            </section>
            <section id="training-review-section" class="member-section" hidden>
              <div class="member-section-head">
                <h3>训练审核</h3>
                <button type="button" id="training-export" class="member-button">导出已批准反馈</button>
              </div>
              <div id="training-feedback-list" class="member-list"></div>
            </section>
          </div>
        </div>
      </div>`;
    document.body.appendChild(memberDialog);

    const annotationDialog = document.createElement("dialog");
    annotationDialog.id = "annotation-dialog";
    annotationDialog.className = "member-dialog";
    annotationDialog.innerHTML = `
      <div class="member-dialog-shell">
        <div class="member-dialog-head">
          <h2 id="annotation-title">股票标注</h2>
          <button type="button" class="member-close-button" aria-label="关闭" title="关闭">×</button>
        </div>
        <div class="member-dialog-body">
          <p id="annotation-message" class="member-message" aria-live="polite"></p>
          <form id="annotation-form" class="member-form">
            <label>形态结论<select name="patternLabel"><option value="watch">继续观察</option><option value="positive">正样本</option><option value="negative">反例</option></select></label>
            <label>周期<select name="timeframe"><option value="daily">日K</option><option value="weekly">周K</option></select></label>
            <label>观察日期<input name="sampleDate" type="date" required></label>
            <label>置信度<select name="confidence"><option value="1">1</option><option value="2">2</option><option value="3" selected>3</option><option value="4">4</option><option value="5">5</option></select></label>
            <label class="member-field-wide">标注<textarea name="note" maxlength="2000"></textarea></label>
            <label class="member-check-label"><input name="nominated" type="checkbox">申请进入训练审核</label>
            <div class="member-form-actions"><button class="member-button primary" type="submit">保存标注</button></div>
          </form>
          <section class="member-section">
            <div class="member-section-head"><h3>成员标注</h3></div>
            <div id="annotation-list" class="member-list"></div>
          </section>
        </div>
      </div>`;
    document.body.appendChild(annotationDialog);

    const liveRegion = document.createElement("div");
    liveRegion.id = "member-live-region";
    liveRegion.className = "member-live-region";
    liveRegion.setAttribute("role", "status");
    liveRegion.hidden = true;
    document.body.appendChild(liveRegion);

    $$(".member-close-button").forEach((button) => {
      button.addEventListener("click", () => closeDialog(button.closest("dialog")));
    });
    $$(".member-dialog").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) closeDialog(dialog);
      });
    });

    $("#member-setup-form").addEventListener("submit", submitSetup);
    $("#member-login-form").addEventListener("submit", submitLogin);
    $("#member-create-form").addEventListener("submit", submitMember);
    $("#annotation-form").addEventListener("submit", submitAnnotation);
    $("#member-logout").addEventListener("click", logout);
    $("#member-refresh").addEventListener("click", refreshMemberData);
    $("#member-import-local").addEventListener("click", importLocalFavorites);
    $("#training-export").addEventListener("click", exportTrainingFeedback);
  }

  function readCodes(key) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(parsed)
        ? parsed.filter((code) => /^\d{6}$/.test(String(code)))
        : [];
    } catch {
      return [];
    }
  }

  function dispatchFavoriteStorage(value) {
    try {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: FAVORITE_KEY,
          newValue: value,
          storageArea: localStorage,
        }),
      );
    } catch {
      window.location.reload();
    }
  }

  function enterCloudMode() {
    if (!localStorage.getItem(CLOUD_MODE_KEY)) {
      localStorage.setItem(
        LOCAL_BACKUP_KEY,
        localStorage.getItem(FAVORITE_KEY) || "[]",
      );
    }
    localStorage.setItem(CLOUD_MODE_KEY, state.user.id);
  }

  function restoreLocalMode() {
    if (!localStorage.getItem(CLOUD_MODE_KEY)) return;
    const value = localStorage.getItem(LOCAL_BACKUP_KEY) || "[]";
    localStorage.setItem(FAVORITE_KEY, value);
    localStorage.removeItem(CLOUD_MODE_KEY);
    dispatchFavoriteStorage(value);
  }

  function syncCloudFavorites() {
    if (!state.user) return;
    enterCloudMode();
    const mine = state.favorites
      .filter((favorite) => favorite.mine)
      .map((favorite) => favorite.code)
      .sort();
    const value = JSON.stringify(mine);
    localStorage.setItem(FAVORITE_KEY, value);
    dispatchFavoriteStorage(value);
  }

  function renderSession() {
    const authView = $("#member-auth-view");
    const setupForm = $("#member-setup-form");
    const loginForm = $("#member-login-form");
    const sessionView = $("#member-session-view");
    const accountButton = $("#member-account-button");
    authView.hidden = Boolean(state.user) || !state.serviceAvailable;
    sessionView.hidden = !state.user;
    setupForm.hidden = !state.serviceAvailable
      || !state.setupRequired
      || !state.setupReady
      || Boolean(state.user);
    loginForm.hidden = !state.serviceAvailable || state.setupRequired || Boolean(state.user);

    if (!state.serviceAvailable) {
      accountButton.textContent = "成员未连接";
      setMessage($("#member-dialog-message"), "成员服务尚未连接", "error");
      return;
    }
    if (!state.user) {
      if (state.setupRequired && !state.setupReady) {
        accountButton.textContent = "初始化未配置";
        setMessage($("#member-dialog-message"), "初始化口令尚未配置", "error");
      } else {
        accountButton.textContent = state.setupRequired ? "初始化成员" : "成员登录";
        setMessage($("#member-dialog-message"));
      }
      return;
    }
    accountButton.textContent = state.user.displayName;
    $("#member-display-name").textContent = state.user.displayName;
    $("#member-role").textContent = state.user.role === "admin" ? "管理员" : "成员";
    const isAdmin = state.user.role === "admin";
    $("#member-admin-section").hidden = !isAdmin;
    $("#training-review-section").hidden = !isAdmin;
    setMessage($("#member-dialog-message"));
    renderSharedFavorites();
    if (isAdmin) {
      renderMembers();
      renderFeedback();
    }
  }

  function makeEmpty(text) {
    const empty = document.createElement("div");
    empty.className = "member-list-empty";
    empty.textContent = text;
    return empty;
  }

  function renderSharedFavorites() {
    const list = $("#shared-favorite-list");
    list.replaceChildren();
    $("#shared-favorite-count").textContent = String(state.favorites.length);
    if (!state.favorites.length) {
      list.appendChild(makeEmpty("暂无共享收藏"));
      return;
    }
    for (const favorite of state.favorites) {
      const row = document.createElement("div");
      row.className = "member-list-row";
      const main = document.createElement("div");
      main.className = "member-list-main";
      const title = document.createElement("strong");
      title.textContent = `${favorite.name} ${favorite.code}`;
      const meta = document.createElement("div");
      meta.className = "member-list-meta";
      meta.textContent = `${favorite.memberCount} 人收藏 · ${favorite.annotationCount} 条标注 · 首次添加 ${favorite.addedBy}`;
      main.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "member-list-actions";
      if (favorite.mine) {
        const mine = document.createElement("span");
        mine.className = "member-chip";
        mine.textContent = "我的收藏";
        actions.appendChild(mine);
      }
      const annotate = createButton("标注");
      annotate.addEventListener("click", () => openAnnotation(favorite.code, favorite.name));
      actions.appendChild(annotate);
      row.append(main, actions);
      list.appendChild(row);
    }
  }

  function renderMembers() {
    const list = $("#member-list");
    list.replaceChildren();
    for (const member of state.members) {
      const row = document.createElement("div");
      row.className = "member-list-row";
      const main = document.createElement("div");
      main.className = "member-list-main";
      const title = document.createElement("strong");
      title.textContent = `${member.displayName} · ${member.username}`;
      const meta = document.createElement("div");
      meta.className = "member-list-meta";
      meta.textContent = `${member.role === "admin" ? "管理员" : "成员"}${member.disabled ? " · 已停用" : ""}`;
      main.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "member-list-actions";
      if (member.role === "member") {
        const toggle = createButton(member.disabled ? "启用" : "停用");
        if (!member.disabled) toggle.classList.add("danger");
        toggle.addEventListener("click", () => toggleMember(member));
        actions.appendChild(toggle);
      }
      row.append(main, actions);
      list.appendChild(row);
    }
  }

  const labelText = {
    watch: "继续观察",
    positive: "正样本",
    negative: "反例",
  };
  const reviewText = {
    not_requested: "未申请",
    pending: "待审核",
    approved: "已批准",
    rejected: "已拒绝",
  };

  function renderFeedback() {
    const list = $("#training-feedback-list");
    list.replaceChildren();
    if (!state.feedback.length) {
      list.appendChild(makeEmpty("暂无训练反馈"));
      return;
    }
    for (const feedback of state.feedback) {
      const row = document.createElement("div");
      row.className = "member-list-row";
      const main = document.createElement("div");
      main.className = "member-list-main";
      const title = document.createElement("strong");
      title.textContent = `${feedback.name} ${feedback.code}`;
      const meta = document.createElement("div");
      meta.className = "member-list-meta";
      meta.textContent = `${feedback.author} · ${labelText[feedback.patternLabel]} · ${feedback.timeframe === "weekly" ? "周K" : "日K"} · ${feedback.sampleDate} · 置信度 ${feedback.confidence} · ${reviewText[feedback.reviewStatus]}`;
      const note = document.createElement("p");
      note.textContent = feedback.note;
      main.append(title, meta, note);
      const actions = document.createElement("div");
      actions.className = "member-list-actions";
      if (feedback.reviewStatus === "pending") {
        const approve = createButton("批准", "member-button primary");
        const reject = createButton("拒绝", "member-button danger");
        approve.addEventListener("click", () => reviewFeedback(feedback.id, "approved"));
        reject.addEventListener("click", () => reviewFeedback(feedback.id, "rejected"));
        actions.append(approve, reject);
      }
      row.append(main, actions);
      list.appendChild(row);
    }
  }

  function renderAnnotations(annotations) {
    const list = $("#annotation-list");
    list.replaceChildren();
    if (!annotations.length) {
      list.appendChild(makeEmpty("暂无成员标注"));
      return;
    }
    for (const annotation of annotations) {
      const row = document.createElement("div");
      row.className = "member-list-row";
      const main = document.createElement("div");
      main.className = "member-list-main";
      const title = document.createElement("strong");
      title.textContent = annotation.author;
      const meta = document.createElement("div");
      meta.className = "member-list-meta";
      meta.textContent = `${labelText[annotation.patternLabel]} · ${annotation.timeframe === "weekly" ? "周K" : "日K"} · ${annotation.sampleDate} · 置信度 ${annotation.confidence} · ${reviewText[annotation.reviewStatus]}`;
      const note = document.createElement("p");
      note.textContent = annotation.note || "-";
      main.append(title, meta, note);
      row.appendChild(main);
      list.appendChild(row);
    }
  }

  async function loadFavorites() {
    const payload = await api("/api/favorites");
    state.favorites = payload.favorites || [];
    syncCloudFavorites();
    renderSharedFavorites();
  }

  async function loadAdminData() {
    if (state.user?.role !== "admin") return;
    const [members, feedback] = await Promise.all([
      api("/api/members"),
      api("/api/training"),
    ]);
    state.members = members.members || [];
    state.feedback = feedback.feedback || [];
    renderMembers();
    renderFeedback();
  }

  async function refreshMemberData() {
    try {
      await Promise.all([loadFavorites(), loadAdminData()]);
      notify("成员数据已刷新", "success");
    } catch (error) {
      handleError(error);
    }
  }

  async function refreshSession() {
    try {
      const payload = await api("/api/auth/me");
      state.serviceAvailable = true;
      state.user = payload.user;
      state.setupRequired = Boolean(payload.setupRequired);
      state.setupReady = Boolean(payload.setupReady);
      if (state.user) {
        enterCloudMode();
        await Promise.all([loadFavorites(), loadAdminData()]);
      } else {
        restoreLocalMode();
      }
    } catch (error) {
      state.serviceAvailable = false;
      state.user = null;
      state.setupRequired = false;
      state.setupReady = false;
      restoreLocalMode();
    }
    renderSession();
  }

  async function openMemberCenter() {
    renderSession();
    openDialog($("#member-dialog"));
    if (state.user) await refreshMemberData();
  }

  async function submitSetup(event) {
    event.preventDefault();
    await submitAuthForm(event.currentTarget, "/api/auth/setup");
  }

  async function submitLogin(event) {
    event.preventDefault();
    await submitAuthForm(event.currentTarget, "/api/auth/login");
  }

  async function submitAuthForm(form, path) {
    const button = $("button[type='submit']", form);
    button.disabled = true;
    const values = Object.fromEntries(new FormData(form));
    try {
      const payload = await api(path, {
        method: "POST",
        body: JSON.stringify(values),
      });
      state.user = payload.user;
      state.setupRequired = false;
      state.setupReady = true;
      enterCloudMode();
      form.reset();
      await Promise.all([loadFavorites(), loadAdminData()]);
      renderSession();
      notify("登录成功", "success");
    } catch (error) {
      setMessage($("#member-dialog-message"), error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } catch {
      // Local state still returns to the pre-login favorites.
    }
    state.user = null;
    state.favorites = [];
    state.members = [];
    state.feedback = [];
    restoreLocalMode();
    renderSession();
    closeDialog($("#member-dialog"));
  }

  async function submitMember(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    button.disabled = true;
    try {
      await api("/api/members", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      form.reset();
      await loadAdminData();
      notify("成员已创建", "success");
    } catch (error) {
      setMessage($("#member-dialog-message"), error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function toggleMember(member) {
    try {
      await api(`/api/members/${member.id}`, {
        method: "PATCH",
        body: JSON.stringify({ disabled: !Boolean(member.disabled) }),
      });
      await loadAdminData();
    } catch (error) {
      handleError(error);
    }
  }

  function stockNames() {
    const map = new Map();
    const roots = [document];
    const template = $("#candidate-row-template");
    if (template) roots.push(template.content);
    for (const root of roots) {
      for (const button of $$('[data-favorite-code]', root)) {
        map.set(button.dataset.favoriteCode, button.dataset.favoriteName);
      }
    }
    return map;
  }

  async function importLocalFavorites() {
    const codes = readCodes(LOCAL_BACKUP_KEY);
    if (!codes.length) {
      notify("没有可导入的本机收藏");
      return;
    }
    const names = stockNames();
    try {
      const payload = await api("/api/favorites/import", {
        method: "POST",
        body: JSON.stringify({
          items: codes.slice(0, 50).map((code) => ({
            code,
            name: names.get(code) || code,
          })),
        }),
      });
      state.favorites = payload.favorites || [];
      syncCloudFavorites();
      renderSharedFavorites();
      notify("本机收藏已导入", "success");
    } catch (error) {
      handleError(error);
    }
  }

  async function toggleFavorite(button) {
    const code = button.dataset.favoriteCode;
    const name = button.dataset.favoriteName;
    const mine = state.favorites.some(
      (favorite) => favorite.code === code && favorite.mine,
    );
    button.disabled = true;
    try {
      const payload = await api(
        mine ? `/api/favorites/${code}` : "/api/favorites",
        mine
          ? { method: "DELETE" }
          : { method: "POST", body: JSON.stringify({ code, name }) },
      );
      state.favorites = payload.favorites || [];
      syncCloudFavorites();
      renderSharedFavorites();
    } catch (error) {
      handleError(error);
    } finally {
      button.disabled = false;
    }
  }

  function enhanceAnnotationButtons() {
    const roots = [document];
    const template = $("#candidate-row-template");
    if (template) roots.push(template.content);
    const seen = new Set();
    for (const root of roots) {
      for (const actions of $$(".stock-actions", root)) {
        if (seen.has(actions) || $(".member-annotation-button", actions)) continue;
        seen.add(actions);
        const scope = actions.closest("tr, .candidate");
        const favorite = scope?.querySelector("[data-favorite-code]");
        if (!favorite) continue;
        const button = createButton("标注", "member-annotation-button");
        button.dataset.code = favorite.dataset.favoriteCode;
        button.dataset.name = favorite.dataset.favoriteName;
        button.addEventListener("click", () => {
          if (!state.user) {
            openMemberCenter();
            setMessage($("#member-dialog-message"), "登录后可共享收藏和标注", "error");
            return;
          }
          openAnnotation(button.dataset.code, button.dataset.name);
        });
        actions.appendChild(button);
      }
    }
  }

  async function openAnnotation(code, name) {
    state.currentStock = { code, name };
    $("#annotation-title").textContent = `${name} ${code}`;
    setMessage($("#annotation-message"), "正在读取标注");
    const form = $("#annotation-form");
    form.reset();
    form.elements.sampleDate.value = new Date().toISOString().slice(0, 10);
    openDialog($("#annotation-dialog"));
    try {
      const payload = await api(`/api/annotations/${code}`);
      const annotations = payload.annotations || [];
      const mine = annotations.find((annotation) => annotation.userId === state.user.id);
      if (mine) {
        form.elements.patternLabel.value = mine.patternLabel;
        form.elements.timeframe.value = mine.timeframe;
        form.elements.sampleDate.value = mine.sampleDate;
        form.elements.confidence.value = String(mine.confidence);
        form.elements.note.value = mine.note;
        form.elements.nominated.checked = mine.nominated;
      }
      renderAnnotations(annotations);
      setMessage($("#annotation-message"));
    } catch (error) {
      setMessage($("#annotation-message"), error.message, "error");
    }
  }

  async function submitAnnotation(event) {
    event.preventDefault();
    if (!state.currentStock || !state.user) return;
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    const values = Object.fromEntries(new FormData(form));
    values.confidence = Number(values.confidence);
    values.nominated = form.elements.nominated.checked;
    values.name = state.currentStock.name;
    button.disabled = true;
    try {
      const payload = await api(`/api/annotations/${state.currentStock.code}`, {
        method: "PUT",
        body: JSON.stringify(values),
      });
      renderAnnotations(payload.annotations || []);
      await Promise.all([loadFavorites(), loadAdminData()]);
      setMessage($("#annotation-message"), "标注已保存", "success");
    } catch (error) {
      setMessage($("#annotation-message"), error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function reviewFeedback(id, status) {
    try {
      await api(`/api/training/${id}/review`, {
        method: "PUT",
        body: JSON.stringify({ status }),
      });
      await loadAdminData();
    } catch (error) {
      handleError(error);
    }
  }

  async function exportTrainingFeedback() {
    try {
      const response = await fetch("/api/training/export", {
        credentials: "same-origin",
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new ApiError(response.status, payload.error || "导出失败");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `tradea-training-feedback-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      handleError(error);
    }
  }

  function handleError(error) {
    if (error instanceof ApiError && error.status === 401) {
      state.user = null;
      restoreLocalMode();
      renderSession();
      openMemberCenter();
    }
    notify(error.message || "操作失败", "error");
  }

  function installFavoriteInterceptor() {
    document.addEventListener(
      "click",
      (event) => {
        const button = event.target.closest("[data-favorite-code]");
        if (!button || !state.user) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        toggleFavorite(button);
      },
      true,
    );
  }

  buildUi();
  enhanceAnnotationButtons();
  installFavoriteInterceptor();
  refreshSession();
})();
