const bridge = window.AstrBotPluginPage;
await bridge.ready();

const byId = id => document.getElementById(id);
const message = error => error?.message || "请求失败";

async function refreshKeys() {
    const data = await bridge.apiGet("keys/list");
    const select = byId("addKey");
    const tags = byId("keyTags");
    select.replaceChildren();
    tags.replaceChildren();
    const keys = data.keys || [];
    if (!keys.length) {
        const option = document.createElement("option");
        option.textContent = "暂无密钥，请先上传";
        option.value = "";
        option.disabled = true;
        option.selected = true;
        select.append(option);
        tags.textContent = "无";
        return;
    }
    for (const key of keys) {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = key;
        select.append(option);
        const tag = document.createElement("span");
        tag.className = "keyTag";
        tag.append(document.createTextNode(key));
        const button = document.createElement("button");
        button.textContent = "×";
        button.addEventListener("click", async () => {
            if (!window.confirm("删除此密钥？仍被服务器引用的密钥不能删除。")) return;
            button.disabled = true;
            try {
                await bridge.apiPost("keys/delete", {filename: key});
                await refreshKeys();
            } catch (error) {
                byId("uploadStatus").textContent = message(error);
            } finally {
                button.disabled = false;
            }
        });
        tag.append(button);
        tags.append(tag, document.createTextNode(" "));
    }
}

async function refreshServers() {
    const data = await bridge.apiGet("servers/list");
    const tbody = byId("serverTbody");
    tbody.replaceChildren();
    const entries = Object.entries(data.servers || {});
    byId("noServers").style.display = entries.length ? "none" : "block";
    for (const [name, server] of entries) {
        const row = document.createElement("tr");
        for (const value of [name, server.ip, server.user, server.key_name]) {
            const cell = document.createElement("td");
            cell.textContent = value;
            row.append(cell);
        }
        const cell = document.createElement("td");
        const button = document.createElement("button");
        button.textContent = "删除";
        button.addEventListener("click", async () => {
            if (!window.confirm("删除服务器配置：" + name + "？")) return;
            button.disabled = true;
            try {
                await bridge.apiPost("servers/remove", {name});
                await refreshServers();
            } catch (error) {
                byId("addStatus").textContent = message(error);
            } finally {
                button.disabled = false;
            }
        });
        cell.append(button);
        row.append(cell);
        tbody.append(row);
    }
}

byId("uploadKeyBtn").addEventListener("click", async () => {
    const file = byId("keyFile").files[0];
    if (!file) return;
    if (file.size > 64 * 1024) {
        byId("uploadStatus").textContent = "私钥不能超过 64 KiB";
        return;
    }
    try {
        const result = await bridge.upload("keys/upload", file);
        byId("uploadStatus").textContent = "✓ " + result.filename + " 上传成功";
        await refreshKeys();
    } catch (error) {
        byId("uploadStatus").textContent = message(error);
    }
});

byId("addBtn").addEventListener("click", async () => {
    const name = byId("addName").value.trim();
    const ip = byId("addIP").value.trim();
    const user = byId("addUser").value.trim();
    const key_name = byId("addKey").value;
    if (!name || !ip || !user || !key_name) {
        byId("addStatus").textContent = "所有字段均为必填";
        return;
    }
    try {
        await bridge.apiPost("servers/add", {name, ip, user, key_name});
        byId("addStatus").textContent = "✓ " + name + " 已添加";
        await refreshServers();
    } catch (error) {
        byId("addStatus").textContent = message(error);
    }
});

try {
    await Promise.all([refreshKeys(), refreshServers()]);
} catch (error) {
    byId("addStatus").textContent = message(error);
}
