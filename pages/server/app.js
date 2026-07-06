const bridge = window.AstrBotPluginPage;
await bridge.ready();

// ── 刷新密钥下拉 & 标签 ──
async function refreshKeys() {
    const data = await bridge.apiGet("keys/list");
    const select = document.getElementById("addKey");
    const tags = document.getElementById("keyTags");
    const keys = data.keys || [];

    // 下拉列表
    select.innerHTML = keys.length === 0
        ? '<option value="" disabled selected>暂无密钥，请先上传</option>'
        : keys.map(k => `<option value="${k}">${k}</option>`).join("");

    // 标签区域，每条带删除按钮
    if (keys.length === 0) {
        tags.innerHTML = "无";
    } else {
        tags.innerHTML = keys.map(k =>
            `<span class="keyTag">${k}<button class="delKeyBtn" data-filename="${k}">×</button></span>`
        ).join(" · ");

        // 绑定删除事件
        tags.querySelectorAll(".delKeyBtn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const filename = btn.dataset.filename;
                await bridge.apiPost("keys/delete", { filename });
                await Promise.all([refreshKeys(), refreshServers()]);
            });
        });
    }
}

// ── 刷新服务器表格 ──
async function refreshServers() {
    const data = await bridge.apiGet("servers/list");
    const tbody = document.getElementById("serverTbody");
    const noServers = document.getElementById("noServers");
    const servers = data.servers || {};
    const entries = Object.entries(servers);

    if (entries.length === 0) {
        tbody.innerHTML = "";
        noServers.style.display = "block";
        return;
    }
    noServers.style.display = "none";
    tbody.innerHTML = entries.map(([name, s]) =>
        `<tr>
      <td>${name}</td>
      <td>${s.ip}</td>
      <td>${s.user}</td>
      <td>${s.key_name}</td>
      <td><button class="delBtn" data-name="${name}">删除</button></td>
    </tr>`
    ).join("");

    // 绑定删除按钮
    tbody.querySelectorAll(".delBtn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const name = btn.dataset.name;
            await bridge.apiPost("servers/remove", {name});
            await refreshServers();
        });
    });
}

// ── 上传密钥 ──
document.getElementById("uploadKeyBtn").addEventListener("click", async () => {
    const file = document.getElementById("keyFile").files[0];
    if (!file) return;
    try {
        const r = await bridge.upload("keys/upload", file);
        document.getElementById("uploadStatus").textContent = `✓ ${r.filename} 上传成功`;
        await Promise.all([refreshKeys(), refreshServers()]);
    } catch (e) {
        document.getElementById("uploadStatus").textContent = `✗ ${e.message}`;
    }
});

// ── 添加服务器 ──
document.getElementById("addBtn").addEventListener("click", async () => {
    const name = document.getElementById("addName").value.trim();
    const ip = document.getElementById("addIP").value.trim();
    const user = document.getElementById("addUser").value.trim();
    const key_name = document.getElementById("addKey").value;
    const status = document.getElementById("addStatus");

    if (!name || !ip || !user || !key_name) {
        status.textContent = "所有字段均为必填";
        return;
    }
    try {
        await bridge.apiPost("servers/add", {name, ip, user, key_name});
        status.textContent = `✓ ${name} 已添加`;
        document.getElementById("addName").value = "";
        document.getElementById("addIP").value = "";
        await refreshServers();
    } catch (e) {
        status.textContent = `✗ ${e.message}`;
    }
});

// ── 初始化 ──
refreshKeys();
refreshServers();