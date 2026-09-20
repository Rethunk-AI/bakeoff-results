"""Admin HTML for the distributed-worker roster and queue."""

from __future__ import annotations

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bakeoff runners</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0 0.8rem 2rem; line-height: 1.5; }
    input, button { padding: 0.4rem 0.7rem; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border-bottom: 1px solid #ddd; padding: 0.5rem; text-align: left; }
    th { background: #f6f8fa; }
    .badge { display: inline-block; font-size: 0.72em; font-weight: 600;
      text-transform: uppercase; padding: 1px 6px; border-radius: 3px; }
    .ACTIVE { color: #1a7f37; background: #dafbe1; }
    .IDLE { color: #0969da; background: #ddf4ff; }
    .DEAD { color: #cf222e; background: #ffebe9; }
    .PENDING { color: #9a6700; background: #fff8c5; }
    .CLAIMED, .IN_PROGRESS { color: #0969da; background: #ddf4ff; }
    .COMPLETE { color: #1a7f37; background: #dafbe1; }
    .FAILED, .CANCELLED { color: #cf222e; background: #ffebe9; }
    .error { color: #cf222e; }
    .toolbar { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0; }
    section { margin-top: 1.5rem; }
  </style>
</head>
<body>
  <h1>Bakeoff runners</h1>
  <p>Roster, heartbeats, and queue for distributed workers. Admin token stays in
  this browser; it is sent as a bearer token and is not written into the page.</p>
  <div class="toolbar">
    <input id="token" type="password" placeholder="Admin token" autocomplete="off">
    <button id="save">Save token</button>
    <button id="refresh">Refresh</button>
  </div>
  <p id="status"></p>
  <section>
    <h2>Whitelist a runner key</h2>
    <div class="toolbar">
      <input id="pubkey" placeholder="Ed25519 public key (base64)" size="40">
      <button data-action="add">Approve</button>
      <button data-action="remove">Revoke</button>
    </div>
  </section>
  <section>
    <h2>Runners</h2>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>Status</th><th>Host</th><th>Heartbeat</th>
          <th>Claim</th><th>VRAM</th><th></th>
        </tr>
      </thead>
      <tbody id="runners"></tbody>
    </table>
  </section>
  <section>
    <h2>Queue <span id="queue-meta"></span></h2>
    <table>
      <thead>
        <tr>
          <th>Job</th><th>Model</th><th>Status</th><th>Priority</th>
          <th>Claimed by</th><th></th>
        </tr>
      </thead>
      <tbody id="jobs"></tbody>
    </table>
  </section>
  <script>
    const tokenInput = document.getElementById("token");
    const statusEl = document.getElementById("status");
    tokenInput.value = sessionStorage.getItem("bakeoff-admin-token") || "";

    function token() { return tokenInput.value.trim(); }
    function headers() {
      return { "Authorization": "Bearer " + token(), "Content-Type": "application/json" };
    }
    function badge(value) {
      return '<span class="badge ' + value + '">' + value + '</span>';
    }
    async function api(path, options) {
      const response = await fetch(path, Object.assign({ headers: headers() }, options));
      if (response.status === 204) return null;
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    }
    async function refresh() {
      statusEl.textContent = "";
      statusEl.className = "";
      try {
        const [runners, queue] = await Promise.all([
          api("/api/runners"),
          api("/api/queue"),
        ]);
        document.getElementById("queue-meta").textContent =
          "(depth " + queue.depth + ", in flight " + queue.in_flight + ")";
        document.getElementById("runners").innerHTML = runners.runners.map(runner => {
          const caps = runner.capabilities || {};
          return "<tr><td>" + runner.runner_id + "</td><td>" + badge(runner.status) +
            "</td><td>" + (runner.hostname || "") + "</td><td>" +
            (runner.last_heartbeat || "") + "</td><td>" +
            (runner.current_claim || "") + "</td><td>" +
            (caps.vram_mb || "") + "</td><td>" +
            "<button data-runner='" + runner.runner_id + "' data-status='IDLE'>Pause</button> " +
            "<button data-runner='" + runner.runner_id + "' data-status='ACTIVE'>Resume</button> " +
            "<button data-key='" + (runner.public_key || "") + "' data-action='remove'>Revoke</button>" +
            "</td></tr>";
        }).join("") || "<tr><td colspan=7>No runners registered.</td></tr>";
        document.getElementById("jobs").innerHTML = queue.jobs.map(job => {
          const id = job.queue_id || job.run_id;
          return "<tr><td>" + id + "</td><td>" + (job.model_id || "") +
            "</td><td>" + badge(job.status) + "</td><td>" + job.priority +
            "</td><td>" + (job.claimed_by || "") + "</td><td>" +
            "<button data-requeue='" + id + "'>Re-queue</button></td></tr>";
        }).join("") || "<tr><td colspan=6>Queue is empty.</td></tr>";
      } catch (err) {
        statusEl.textContent = err.message;
        statusEl.className = "error";
      }
    }
    document.getElementById("save").onclick = () => {
      sessionStorage.setItem("bakeoff-admin-token", token());
      refresh();
    };
    document.getElementById("refresh").onclick = refresh;
    document.body.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      try {
        if (button.dataset.action) {
          const key = button.dataset.key || document.getElementById("pubkey").value.trim();
          await api("/api/admin/keys", {
            method: "POST",
            body: JSON.stringify({ action: button.dataset.action, public_key: key }),
          });
        } else if (button.dataset.runner) {
          await api("/api/admin/runners/" + button.dataset.runner + "/status", {
            method: "POST",
            body: JSON.stringify({ status: button.dataset.status }),
          });
        } else if (button.dataset.requeue) {
          await api("/api/queue/" + button.dataset.requeue + "/requeue", { method: "POST" });
        } else {
          return;
        }
        await refresh();
      } catch (err) {
        statusEl.textContent = err.message;
        statusEl.className = "error";
      }
    });
    if (token()) refresh();
  </script>
</body>
</html>
"""
